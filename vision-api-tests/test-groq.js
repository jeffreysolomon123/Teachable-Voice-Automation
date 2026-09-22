require('dotenv').config();
const fs = require('fs');
const sharp = require('sharp'); // npm install sharp
const Groq = require('groq-sdk');

const groq = new Groq({ apiKey: process.env.QROQ_API_KEY });

// The instruction describing what we're trying to find/tap on this screen.
// In the real pipeline this comes from your semantic step description,
// not a hardcoded string.
const TARGET_INSTRUCTION = 'the "Add to cart" button';

// Keep this in sync with whatever width you actually resize to below —
// the model's returned x/y are relative to THIS size, not the original
// screenshot resolution. You scale back up to real device pixels after
// you get the response (see scaleCoordinates below).
const SENT_IMAGE_WIDTH = 700;

async function compressImage(inputPath) {
  // Resize to a fixed width (preserving aspect ratio) and re-encode as
  // JPEG at moderate quality. This does two things: cuts upload size/time,
  // and reduces how many "tiles"/tokens the model has to process, which
  // is a meaningful chunk of latency on top of output-length.
  const outputBuffer = await sharp(inputPath)
    .resize({ width: SENT_IMAGE_WIDTH })
    .jpeg({ quality: 70 })
    .toBuffer();

  // Also grab the resulting dimensions — needed later to scale coordinates
  // back to the real screen size accurately (height may not be exactly
  // what you expect once aspect ratio is preserved).
  const metadata = await sharp(outputBuffer).metadata();

  return {
    base64: outputBuffer.toString('base64'),
    sentWidth: metadata.width,
    sentHeight: metadata.height,
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
  // Defensively strip a <think>...</think> block in case the model still
  // emits one despite reasoning_effort being set to "none" — cheap
  // insurance against a parsing crash if that ever happens.
  const withoutThinking = raw.replace(/<think>[\s\S]*?<\/think>/i, '').trim();

  const cleaned = withoutThinking
    .replace(/^```json/i, '')
    .replace(/^```/, '')
    .replace(/```$/, '')
    .trim();
  return JSON.parse(cleaned);
}

// Scale coordinates from the (compressed) sent-image space back to the
// real device screen. realDeviceWidth/Height should be the actual
// screenshot resolution before compression (e.g. from your Android
// AccessibilityService capture).
function scaleCoordinates(x, y, sentWidth, sentHeight, realDeviceWidth, realDeviceHeight) {
  return {
    x: Math.round(x * (realDeviceWidth / sentWidth)),
    y: Math.round(y * (realDeviceHeight / sentHeight)),
  };
}

async function findUiElement() {
  const imagePath = './ss1.jpeg';
  // Set these to the real screenshot's actual resolution before compression.
  const REAL_DEVICE_WIDTH = 1220;
  const REAL_DEVICE_HEIGHT = 2712;

  try {
    console.log('Compressing image...');
    const { base64, sentWidth, sentHeight } = await compressImage(imagePath);
    console.log(`Sent image size: ${sentWidth}x${sentHeight}`);

    const prompt = buildPrompt(TARGET_INSTRUCTION, sentWidth, sentHeight);

    console.log('Querying Groq...');
    const t0 = Date.now();

    const response = await groq.chat.completions.create({
      model: 'qwen/qwen3.6-27b', // double-check this against your account's available models
      messages: [
        {
          role: 'user',
          content: [
            { type: 'text', text: prompt },
            {
              type: 'image_url',
              image_url: { url: `data:image/jpeg;base64,${base64}` },
            },
          ],
        },
      ],
      max_tokens: 150, // small buffer in case any reasoning slips through
      temperature: 0,  // deterministic-ish grounding, not creative writing
      reasoning_effort: 'none', // this model defaults to "thinking mode" —
                                 // this switches it to fast instruct mode,
                                 // which is what we want for a quick lookup
    });

    const elapsedMs = Date.now() - t0;
    const rawText = response.choices[0].message.content;

    console.log(`\n=== RAW RESPONSE (${elapsedMs}ms) ===`);
    console.log(rawText);
    console.log('=====================\n');

    const result = parseModelResponse(rawText);

    if (result.found) {
      const real = scaleCoordinates(
        result.x, result.y, sentWidth, sentHeight,
        REAL_DEVICE_WIDTH, REAL_DEVICE_HEIGHT
      );
      console.log('Found:', result);
      console.log('Scaled to real device coordinates:', real);
    } else {
      console.log('Not found:', result.reason);
    }

  } catch (error) {
    console.error('Error:', error.message);
  }
}

findUiElement();