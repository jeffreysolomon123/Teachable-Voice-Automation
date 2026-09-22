require('dotenv').config();
const fs = require('fs');
const sharp = require('sharp'); // npm install sharp
const Tesseract = require('tesseract.js'); // npm install tesseract.js
const Groq = require('groq-sdk'); // npm install groq-sdk

const groq = new Groq({ apiKey: process.env.QROQ_API_KEY });

const IMAGE_PATH = './ss1.jpeg';
const TARGET_INSTRUCTION = 'the "Add to cart" button';
const SENT_IMAGE_WIDTH = 700;

// ---------------------------------------------------------------------------
// Step 1: compress the screenshot (same as before) and remember the real
// resolution so we can scale the final answer back up at the end.
// ---------------------------------------------------------------------------
async function compressImage(inputPath) {
  const originalMetadata = await sharp(inputPath).metadata();
  const outputBuffer = await sharp(inputPath)
    .resize({ width: SENT_IMAGE_WIDTH })
    .jpeg({ quality: 80 })
    .toBuffer();
  const sentMetadata = await sharp(outputBuffer).metadata();

  return {
    buffer: outputBuffer,
    sentWidth: sentMetadata.width,
    sentHeight: sentMetadata.height,
    realWidth: originalMetadata.width,
    realHeight: originalMetadata.height,
  };
}

// ---------------------------------------------------------------------------
// Step 2: run OCR on the (already compressed) image and get real bounding
// boxes for each detected line of text. Running OCR on the SAME image we
// send to the LLM means the candidate coordinates and the image the model
// sees are in the same coordinate space — no extra conversion to track.
// ---------------------------------------------------------------------------
async function detectTextCandidates(imageBuffer, sentWidth, sentHeight) {
  // We proved (via a manual crop test) that running Tesseract on the WHOLE
  // page causes its layout analysis to misclassify solid-color buttons as
  // graphics and skip them entirely -- even though it reads the exact same
  // pixels perfectly when given just that region in isolation. The fix:
  // slice the image into overlapping horizontal bands and OCR each one
  // separately, so there's never a whole-page layout judgment to get wrong.
  const BAND_HEIGHT = 320;
  const OVERLAP = 60;

  const { createWorker } = Tesseract;
  const worker = await createWorker('eng');

  const allLines = [];

  for (let top = 0; top < sentHeight; top += (BAND_HEIGHT - OVERLAP)) {
    const height = Math.min(BAND_HEIGHT, sentHeight - top);
    const bandBuffer = await sharp(imageBuffer)
      .extract({ left: 0, top, width: sentWidth, height })
      .toBuffer();

    const { data } = await worker.recognize(bandBuffer, {}, { blocks: true });

    let rawLines = [];
    if (Array.isArray(data.lines)) {
      rawLines = data.lines;
    } else if (Array.isArray(data.blocks)) {
      for (const block of data.blocks) {
        for (const paragraph of block.paragraphs || []) {
          for (const line of paragraph.lines || []) {
            rawLines.push(line);
          }
        }
      }
    }

    // bbox coordinates from a cropped band are relative to that band --
    // shift them back into full-image coordinates by adding this band's
    // top offset.
    for (const line of rawLines) {
      if (!line.text || line.text.trim().length === 0 || line.confidence <= 40) continue;
      allLines.push({
        text: line.text.trim(),
        x0: line.bbox.x0,
        y0: line.bbox.y0 + top,
        x1: line.bbox.x1,
        y1: line.bbox.y1 + top,
      });
    }
  }

  await worker.terminate();

  // The overlap between bands means some lines get detected twice. Dedupe
  // by treating two detections as the same line if they have identical
  // text and are within a few pixels of each other vertically.
  const deduped = [];
  for (const line of allLines) {
    const isDuplicate = deduped.some(existing =>
      existing.text === line.text && Math.abs(existing.y0 - line.y0) < 15
    );
    if (!isDuplicate) deduped.push(line);
  }

  const candidates = deduped.map(line => ({
    text: line.text,
    x0: line.x0, y0: line.y0, x1: line.x1, y1: line.y1,
    centerX: Math.round((line.x0 + line.x1) / 2),
    centerY: Math.round((line.y0 + line.y1) / 2),
  }));

  return candidates.slice(0, 60); // slightly higher cap since tiling finds more real text
}

// ---------------------------------------------------------------------------
// Step 3: draw a numbered marker at each candidate's location, directly onto
// the image, via an SVG overlay composited with sharp. This is the "Set of
// Mark" step — we're turning "guess a coordinate" into "pick a number".
// ---------------------------------------------------------------------------
async function annotateWithMarks(imageBuffer, candidates, width, height) {
  const marks = candidates.map((c, i) => `
    <circle cx="${c.centerX}" cy="${c.y0 - 8}" r="11" fill="red" fill-opacity="0.85" />
    <text x="${c.centerX}" y="${c.y0 - 4}" font-size="14" font-weight="bold"
          fill="white" text-anchor="middle" dominant-baseline="middle">${i}</text>
  `).join('');

  const svgOverlay = `<svg width="${width}" height="${height}">${marks}</svg>`;

  return sharp(imageBuffer)
    .composite([{ input: Buffer.from(svgOverlay), top: 0, left: 0 }])
    .jpeg({ quality: 85 })
    .toBuffer();
}

// ---------------------------------------------------------------------------
// Step 4: ask the LLM to pick a NUMBER, not a coordinate. This is the part
// Set-of-Mark is meant to fix — models are much better at multiple-choice
// selection than freehand spatial reasoning, which is exactly what went
// wrong in the raw-coordinate tests.
// ---------------------------------------------------------------------------
function buildPrompt(instruction, candidates) {
  const candidateList = candidates
    .map((c, i) => `${i}: "${c.text}"`)
    .join('\n');

  return `This screenshot has numbered red markers next to pieces of text. Here is what each number's text says:

${candidateList}

Which number's marker points at ${instruction}?

Respond with ONLY valid JSON, no other text, no markdown fences:
{"found": true, "number": <int>}
or
{"found": false, "reason": "<one short phrase>"}`;
}

function parseModelResponse(raw) {
  const cleaned = raw.trim()
    .replace(/<think>[\s\S]*?<\/think>/i, '')
    .replace(/^```json/i, '')
    .replace(/^```/, '')
    .replace(/```$/, '')
    .trim();
  return JSON.parse(cleaned);
}

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------
async function findUiElementSetOfMark() {
  try {
    console.log('Compressing image...');
    const { buffer, sentWidth, sentHeight, realWidth, realHeight } = await compressImage(IMAGE_PATH);
    console.log(`Real size: ${realWidth}x${realHeight} | Sent size: ${sentWidth}x${sentHeight}`);

    console.log('Running OCR to find text candidates...');
    const t0ocr = Date.now();
    const candidates = await detectTextCandidates(buffer, sentWidth, sentHeight);
    console.log(`OCR found ${candidates.length} candidates in ${Date.now() - t0ocr}ms:`);
    candidates.forEach((c, i) => console.log(`  ${i}: "${c.text}" @ (${c.centerX}, ${c.centerY})`));

    if (candidates.length === 0) {
      console.log('No text candidates found — Set-of-Mark needs at least text-based elements. See notes on icon-only buttons.');
      return;
    }

    console.log('\nAnnotating image with numbered markers...');
    const annotatedBuffer = await annotateWithMarks(buffer, candidates, sentWidth, sentHeight);
    fs.writeFileSync('./annotated_debug.jpg', annotatedBuffer); // save so you can eyeball it yourself

    const prompt = buildPrompt(TARGET_INSTRUCTION, candidates);

    console.log('Querying Groq to pick a number...');
    const t0 = Date.now();
    const response = await groq.chat.completions.create({
      model: 'qwen/qwen3.8-27b',
      messages: [{
        role: 'user',
        content: [
          { type: 'text', text: prompt },
          { type: 'image_url', image_url: { url: `data:image/jpeg;base64,${annotatedBuffer.toString('base64')}` } },
        ],
      }],
      max_tokens: 100,
      temperature: 0,
      reasoning_effort: 'none',
    });
    const elapsedMs = Date.now() - t0;

    const rawText = response.choices[0].message.content;
    console.log(`\n=== RAW RESPONSE (${elapsedMs}ms) ===`);
    console.log(rawText);
    console.log('=====================\n');

    const result = parseModelResponse(rawText);

    if (result.found && candidates[result.number]) {
      const chosen = candidates[result.number];
      // Scale the ALREADY-KNOWN, OCR-measured bounding box center back to
      // real device pixels. No coordinate was invented by the model at all
      // — it only chose which known box to use.
      const real = {
        x: Math.round(chosen.centerX * (realWidth / sentWidth)),
        y: Math.round(chosen.centerY * (realHeight / sentHeight)),
      };
      console.log(`Picked #${result.number}: "${chosen.text}"`);
      console.log('Real device coordinates:', real);
    } else {
      console.log('Not found:', result.reason || 'model picked an invalid number');
    }

  } catch (error) {
    console.error('Error:', error.message);
  }
}

findUiElementSetOfMark();