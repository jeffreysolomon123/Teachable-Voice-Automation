const vision = require('@google-cloud/vision');

// 1. Initialize the client using your JSON key file directly
const client = new vision.ImageAnnotatorClient({
  keyFilename: '../teachablevoiceautomation-57b32d9915cf.json',
});

async function runOCR() {
  const imageUri = './ss1.jpeg';

  try {
    console.log('Extracting text with Google Cloud Vision API...\n');

    // 2. Perform OCR text detection on your local image
    const [result] = await client.textDetection(imageUri);
    const detections = result.textAnnotations;

    if (!detections || detections.length === 0) {
      console.log('No text detected in the image.');
      return;
    }

    // The first item in detections contains the ENTIRE extracted text block
    console.log('=== FULL EXTRACTED TEXT ===');
    console.log(detections[0].description);
    console.log('===========================\n');

    // Subsequent items contain individual words/lines with positional bounding boxes
    console.log('--- Individual Words / Phrases Detected ---');
    detections.slice(1).forEach((text) => {
      console.log(`Word: "${text.description}"`);
    });

  } catch (error) {
    console.error('Error connecting to Vision API:', error.message);
  }
}

runOCR();