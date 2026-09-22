require('dotenv').config();
const fs = require('fs');
const sharp = require('sharp'); // npm install sharp
const { GoogleGenAI } = require('@google/genai');

const ai = new GoogleGenAI({ apiKey: process.env.GEMINI_API_KEY });

// The instruction describing what we're trying to find/tap on this screen.
// In the real pipeline this comes from your semantic step description,
// not a hardcoded string.
const TARGET_INSTRUCTION = 'the "Add to cart" button';

const SENT_IMAGE_WIDTH = 700;

async function compressImage(inputPath) {
  // Read the ORIGINAL image's real dimensions first — this is what fixed
  // the bug from the Groq test, where a hardcoded resolution from a
  // different recording silently produced wrong scaled coordinates.
  const originalMetadata = await sharp(inputPath).metadata();

  const outputBuffer = await sharp(inputPath)
    .resize({ width: SENT_IMAGE_WIDTH })
    .jpeg({ quality: 70 })
    .toBuffer();

  const sentMetadata = await sharp(outputBuffer).metadata();

  return {
    base64: outputBuffer.toString('base64'),
    sentWidth: sentMetadata.width,
    sentHeight: sentMetadata.height,
    realWidth: originalMetadata.width,
    realHeight: originalMetadata.height,
  };
}

function buildPrompt(instruction, sentWidth, sentHeight) {
  return `You are looking at a screenshot of a mobile app, exactly ${sentWidth}x${sentHeight} pixels (width x height).

Find ${instruction} in this screenshot.

Respond with ONLY valid JSON, no other text, no markdown code fences, matching exactly this shape:

{"found": true, "x": <int>, "y": <int>, "confidence": "high"|"medium"|"low"}

or, if you cannot find a matching element:

{"found": false, "reason": "<one short phrase>"}

x and y must be pixel coordinates within the image bounds (0 to ${sentWidth} for x, 0 to ${sentHeight} for y), pointing at the center of the element.`;
}

function parseModelResponse(raw) {
  const withoutThinking = raw.replace(/<think>[\s\S]*?<\/think>/i, '').trim();
  const cleaned = withoutThinking
    .replace(/^```json/i, '')
    .replace(/^```/, '')
    .replace(/```$/, '')
    .trim();
  return JSON.parse(cleaned);
}

function scaleCoordinates(x, y, sentWidth, sentHeight, realWidth, realHeight) {
  return {
    x: Math.round(x * (realWidth / sentWidth)),
    y: Math.round(y * (realHeight / sentHeight)),
  };
}

async function findUiElement() {
  const imagePath = './ss1.jpeg';

  try {
    console.log('Compressing image...');
    const { base64, sentWidth, sentHeight, realWidth, realHeight } = await compressImage(imagePath);
    console.log(`Real image size: ${realWidth}x${realHeight}`);
    console.log(`Sent image size: ${sentWidth}x${sentHeight}`);

    const prompt = buildPrompt(TARGET_INSTRUCTION, sentWidth, sentHeight);

    console.log('Querying Gemini...');
    const t0 = Date.now();

    const response = await ai.models.generateContent({
      model: 'gemini-2.5-flash',
      contents: [
        {
          role: 'user',
          parts: [
            { text: prompt },
            { inlineData: { mimeType: 'image/jpeg', data: base64 } },
          ],
        },
      ],
      generationConfig: {
        responseMimeType: 'application/json', // ask Gemini to enforce JSON output natively
        temperature: 0,
        maxOutputTokens: 150,
      },
    });

    const elapsedMs = Date.now() - t0;
    const rawText = response.text;

    console.log(`\n=== RAW RESPONSE (${elapsedMs}ms) ===`);
    console.log(rawText);
    console.log('=====================\n');

    const result = parseModelResponse(rawText);

    if (result.found) {
      const real = scaleCoordinates(result.x, result.y, sentWidth, sentHeight, realWidth, realHeight);
      console.log('Found:', result);
      console.log('Scaled to real device coordinates:', real);
    } else {
      console.log('Not found:', result.reason);
    }

  } catch (error) {
    console.error('Error connecting to Gemini API:', error.message);
  }
}

findUiElement();