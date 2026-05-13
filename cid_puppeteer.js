const puppeteer = require('puppeteer');

async function getCIDViaPuppeteer(cleanIid) {
  const { CIDError } = require('./cid_helper');
  let browser;
  try {
    console.log('[Puppeteer] Fetching dynamic govUrlID...');
    const configResp = await fetch('https://visualsupport.microsoft.com/api/configuration/govUrlID');
    if (!configResp.ok) {
      throw new Error(`Failed to fetch govUrlID: HTTP ${configResp.status}`);
    }
    const config = await configResp.json();
    const govId = config.govUrlID;
    if (!govId) {
      throw new Error('govUrlID not found in configuration response');
    }
    console.log('[Puppeteer] dynamic govUrlID retrieved:', govId);

    const launchOptions = {
      headless: true,
      args: [
        '--no-sandbox',
        '--disable-setuid-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
        '--disable-blink-features=AutomationControlled'
      ]
    };
    if (process.env.PUPPETEER_EXECUTABLE_PATH) {
      launchOptions.executablePath = process.env.PUPPETEER_EXECUTABLE_PATH;
    }
    browser = await puppeteer.launch(launchOptions);

    const page = await browser.newPage();
    await page.setViewport({ width: 1280, height: 900 });

    // Set user agent to avoid basic blocks
    await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');

    const govUrl = `https://visualsupport.microsoft.com/${govId}`;
    console.log('[Puppeteer] STEP 1: Navigating to portal:', govUrl);
    await page.goto(govUrl, { waitUntil: 'networkidle2', timeout: 40000 });

    console.log('[Puppeteer] STEP 2: Clicking "Activate a Microsoft Product"');
    await page.waitForSelector('text/Activate a Microsoft Product', { timeout: 15000 });
    await page.click('text/Activate a Microsoft Product');
    await new Promise(r => setTimeout(r, 2000));

    console.log('[Puppeteer] STEP 3: Selecting product option (Windows)');
    await page.waitForSelector('img[alt="Windows"]', { timeout: 15000 });
    await page.click('img[alt="Windows"]');
    await new Promise(r => setTimeout(r, 2000));

    const is6Digits = cleanIid.length <= 54;
    const digitOption = is6Digits ? '6 Digits' : '7 Digits';
    console.log(`[Puppeteer] STEP 4: Clicking "${digitOption}" option`);
    
    const clickedDigits = await page.evaluate((optionText) => {
      const els = Array.from(document.querySelectorAll('*'));
      for (const el of els) {
        if (el.textContent.trim() === optionText && el.offsetParent !== null) {
          el.click();
          return true;
        }
      }
      return false;
    }, digitOption);

    if (!clickedDigits) {
      await page.click(`text/${digitOption}`);
    }
    await new Promise(r => setTimeout(r, 2000));

    console.log('[Puppeteer] STEP 5: Entering complete Installation ID');
    await page.waitForSelector('input[type="text"]', { timeout: 15000 });

    const focusedInput = await page.evaluate(() => {
      const inputs = Array.from(document.querySelectorAll('input[type="text"]'));
      const mainInput = inputs.find(i => i.placeholder && i.placeholder.toLowerCase().includes('complete')) || inputs[0];
      if (mainInput) {
        mainInput.focus();
        return true;
      }
      return false;
    });

    if (focusedInput) {
      await page.keyboard.type(cleanIid, { delay: 5 });
    } else {
      throw new Error('Could not focus Installation ID input field');
    }

    // Wait for validation to occur inline
    await new Promise(r => setTimeout(r, 2500));

    // Check if submit button is available/enabled, or if there are validation warnings
    console.log('[Puppeteer] STEP 6: Checking validation and submitting');
    const state = await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      const submitBtn = btns.find(b => b.textContent.trim() === 'Submit');
      const isDisabled = !submitBtn || submitBtn.disabled;
      
      // Look for warning triangles or error texts
      const pageText = document.body.innerText.toLowerCase();
      const hasChecksumWarning = isDisabled && (document.querySelectorAll('svg').length > 10 || pageText.includes('invalid') || pageText.includes('check'));
      
      if (submitBtn && !submitBtn.disabled) {
        submitBtn.click();
        return { success: true };
      }
      return { success: false, isDisabled, hasChecksumWarning, pageText: document.body.innerText };
    });

    if (!state.success) {
      console.log('[Puppeteer] Submit disabled or warning detected. State:', JSON.stringify(state));
      await browser.close();
      throw new CIDError('INVALID_CHECKSUM', '❌ *IID con checksum inválido*\nUn dígito está incorrecto. Verifica cada bloque contra tu pantalla.', { iid: cleanIid });
    }

    console.log('[Puppeteer] STEP 7: Waiting for activation result...');
    // Wait up to 25 seconds for response page to load
    await new Promise(r => setTimeout(r, 6000));
    
    // Let's poll for CID blocks or error text
    let cidBlocks = null;
    let msErrorText = '';
    
    for (let attempt = 0; attempt < 10; attempt++) {
      const result = await page.evaluate(() => {
        const text = document.body.innerText;
        // Look for blocks of 6 digits in the confirmation view
        // Usually presented as 8 or 9 blocks labeled A, B, C...
        const matches = text.match(/\b\d{6}\b/g) || [];
        // Filter out common non-CID 6-digit sequences if any, but in confirmation screen mostly CID digits exist
        // If we find at least 8 blocks of 6 digits, it's our CID!
        if (matches.length >= 8) {
          return { found: true, blocks: matches.slice(0, 8), fullText: text };
        }
        return { found: false, fullText: text };
      });

      if (result.found) {
        cidBlocks = result.blocks;
        console.log('[Puppeteer] Successful activation! CID retrieved.');
        break;
      } else {
        msErrorText = result.fullText;
        await new Promise(r => setTimeout(r, 2000));
      }
    }

    await browser.close();

    if (cidBlocks) {
      return cidBlocks.join('-');
    }

    // If no CID found, parse the error text from the page
    console.log('[Puppeteer] Activation failed or no CID blocks found. Page text:', msErrorText.substring(0, 500));
    const lowerText = msErrorText.toLowerCase();
    
    if (lowerText.includes('blocked') || lowerText.includes('bloqueada')) {
      throw new CIDError('KEY_BLOCKED', '🔒 *Clave bloqueada por Microsoft*\nEsta licencia ha sido bloqueada. Contacta soporte para un reemplazo.', { iid: cleanIid });
    }
    if (lowerText.includes('limit') || lowerText.includes('exceeded') || lowerText.includes('too many') || lowerText.includes('límite')) {
      throw new CIDError('TOO_MANY_ACTIVATIONS', '⚠️ *Límite de activaciones alcanzado*\nEsta licencia ya se activó en demasiados dispositivos. Contacta soporte.', { iid: cleanIid });
    }
    if (lowerText.includes('not genuine') || lowerText.includes('no válida')) {
      throw new CIDError('KEY_NOT_GENUINE', '🚫 *Licencia no válida.*\nContacta soporte.', { iid: cleanIid });
    }
    if (lowerText.includes('expired') || lowerText.includes('expirada')) {
      throw new CIDError('KEY_EXPIRED', '⏰ *Licencia expirada.*\nContacta soporte.', { iid: cleanIid });
    }
    if (lowerText.includes('support') || lowerText.includes('unsupported') || lowerText.includes('soportado')) {
      throw new CIDError('INVALID_PRODUCT', '❌ *Producto no soportado para activación telefónica.*', { iid: cleanIid });
    }

    throw new CIDError('ACTIVATION_FAILED', `❌ *Activación rechazada por Microsoft*\nNo se pudo generar el Confirmation ID para este IID.\n\nDetalles:\n${msErrorText.substring(0, 200)}`, { iid: cleanIid });

  } catch (err) {
    if (browser) {
      try { await browser.close(); } catch (_) {}
    }
    if (err instanceof CIDError) {
      throw err;
    }
    throw new CIDError('NETWORK_ERROR', `❌ *Error interno de automatización:* ${err.message}`, { iid: cleanIid });
  }
}

module.exports = { getCIDViaPuppeteer };
