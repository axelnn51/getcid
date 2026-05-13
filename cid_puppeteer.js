const puppeteer = require('puppeteer');

// Global Cache to eliminate startup overhead
let globalBrowser = null;
let globalGovUrlID = null;
let lastGovUrlFetchTime = 0;

async function getCachedGovUrlID() {
  const now = Date.now();
  // Fetch a new ID if we don't have one or if it's older than 3 hours
  if (!globalGovUrlID || now - lastGovUrlFetchTime > 3 * 60 * 60 * 1000) {
    console.log('[Puppeteer] Fetching new dynamic govUrlID...');
    const configResp = await fetch('https://visualsupport.microsoft.com/api/configuration/govUrlID');
    if (!configResp.ok) throw new Error(`Failed to fetch govUrlID: HTTP ${configResp.status}`);
    const config = await configResp.json();
    globalGovUrlID = config.govUrlID;
    lastGovUrlFetchTime = now;
    console.log('[Puppeteer] dynamic govUrlID cached:', globalGovUrlID);
  }
  return globalGovUrlID;
}

async function getCachedBrowser() {
  if (!globalBrowser || !globalBrowser.isConnected()) {
    console.log('[Puppeteer] Launching persistent headless browser instance...');
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
    globalBrowser = await puppeteer.launch(launchOptions);
    
    // Handle unexpected disconnections
    globalBrowser.on('disconnected', () => {
      console.log('[Puppeteer] Persistent browser disconnected. Will relaunch on next request.');
      globalBrowser = null;
    });
  }
  return globalBrowser;
}

async function executePuppeteerAttempt(cleanIid, productHint, isRetry = false) {
  const { CIDError } = require('./cid_helper');
  const govId = await getCachedGovUrlID();
  const browser = await getCachedBrowser();

  const page = await browser.newPage();
  try {
    await page.setViewport({ width: 1280, height: 900 });
    await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');

    const govUrl = `https://visualsupport.microsoft.com/${govId}`;
    console.log(`[Puppeteer] Navigating to portal (${productHint === 'office' ? 'Office' : 'Windows'} path)...`);
    // Reduced timeout and faster waitUntil since base browser is warm
    await page.goto(govUrl, { waitUntil: 'domcontentloaded', timeout: 25000 });

    // Step 2: Click Activate a Microsoft Product
    await page.waitForSelector('text/Activate a Microsoft Product', { timeout: 10000 });
    await page.click('text/Activate a Microsoft Product');
    // Minimal reliable wait
    await new Promise(r => setTimeout(r, 600));

    // Step 3: Select Product Option
    if (productHint === 'office') {
      console.log('[Puppeteer] Selecting product option: Microsoft Office');
      // Look for Office image or text
      const clickedOffice = await page.evaluate(() => {
        const imgs = Array.from(document.querySelectorAll('img'));
        const officeImg = imgs.find(img => img.alt && img.alt.toLowerCase().includes('office'));
        if (officeImg && officeImg.offsetParent !== null) {
          officeImg.click();
          return true;
        }
        const els = Array.from(document.querySelectorAll('*'));
        for (const el of els) {
          if (el.textContent.trim() === 'Microsoft Office' && el.offsetParent !== null) {
            el.click();
            return true;
          }
        }
        return false;
      });
      if (!clickedOffice) {
        // Fallback selector
        await page.click('img[alt*="Office"]').catch(() => page.click('text/Microsoft Office'));
      }
    } else {
      console.log('[Puppeteer] Selecting product option: Windows');
      await page.waitForSelector('img[alt="Windows"]', { timeout: 10000 });
      await page.click('img[alt="Windows"]');
    }
    await new Promise(r => setTimeout(r, 600));

    // Step 4: Select block length (6 vs 7 Digits)
    const is6Digits = cleanIid.length <= 54;
    const digitOption = is6Digits ? '6 Digits' : '7 Digits';
    
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
    await new Promise(r => setTimeout(r, 600));

    // Step 5: Enter complete Installation ID
    await page.waitForSelector('input[type="text"]', { timeout: 10000 });
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
      // Extremely fast typing
      await page.keyboard.type(cleanIid, { delay: 1 });
    } else {
      throw new Error('Could not focus Installation ID input field');
    }

    // Wait for inline JS validation to unlock submit button
    await new Promise(r => setTimeout(r, 1500));

    // Step 6: Check validation & Submit
    const state = await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      const submitBtn = btns.find(b => b.textContent.trim() === 'Submit');
      const isDisabled = !submitBtn || submitBtn.disabled;
      
      if (submitBtn && !submitBtn.disabled) {
        submitBtn.click();
        return { success: true };
      }
      return { success: false, isDisabled };
    });

    if (!state.success) {
      await page.close().catch(() => {});
      throw new CIDError('INVALID_CHECKSUM', '❌ *IID con checksum inválido*\nUn dígito está incorrecto. Verifica cada bloque contra tu pantalla.', { iid: cleanIid });
    }

    // Step 7: Fast polling for CID result blocks or rejection
    let cidBlocks = null;
    let msErrorText = '';

    for (let attempt = 0; attempt < 12; attempt++) {
      await new Promise(r => setTimeout(r, 1000));
      const result = await page.evaluate(() => {
        const text = document.body.innerText;
        const matches = text.match(/\b\d{6}\b/g) || [];
        if (matches.length >= 8) {
          return { found: true, blocks: matches.slice(0, 8), fullText: text };
        }
        return { found: false, fullText: text };
      });

      if (result.found) {
        cidBlocks = result.blocks;
        break;
      } else {
        msErrorText = result.fullText;
        // If response explicitly loaded with rejection, break early to save polling time
        const lowerText = msErrorText.toLowerCase();
        if (lowerText.includes('displayed in your product') || lowerText.includes('enter installation id')) {
          // Still on input page loading
          continue;
        }
        if (lowerText.length > 50 && (lowerText.includes('reject') || lowerText.includes('rechaz') || lowerText.includes('unsuccessful') || lowerText.includes('failed') || lowerText.includes('not genuine') || lowerText.includes('blocked') || lowerText.includes('exceeded'))) {
          break;
        }
      }
    }

    await page.close().catch(() => {});

    if (cidBlocks) {
      return { success: true, cid: cidBlocks.join('-') };
    }

    const lowerText = msErrorText.toLowerCase();
    
    // Check critical MS non-retryable errors
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

    return { success: false, errorText: msErrorText };

  } catch (err) {
    await page.close().catch(() => {});
    if (err instanceof CIDError) throw err;
    throw new Error(err.message);
  }
}

async function getCIDViaPuppeteer(cleanIid, productHint = 'windows') {
  const { CIDError } = require('./cid_helper');
  try {
    // Attempt 1 using provided or inferred hint
    console.log(`[Puppeteer] Execution attempt 1 starting (Hint: ${productHint})`);
    const result1 = await executePuppeteerAttempt(cleanIid, productHint, false);
    if (result1.success) {
      console.log('[Puppeteer] Success on attempt 1!');
      return result1.cid;
    }

    // If attempt 1 failed with generic rejection (e.g. submitted Office under Windows path),
    // automatically execute extremely fast retry using the OPPOSITE path!
    const oppositeHint = productHint === 'office' ? 'windows' : 'office';
    console.log(`[Puppeteer] Attempt 1 rejected generic activation. Retrying automatically with opposite path: ${oppositeHint}...`);
    
    const result2 = await executePuppeteerAttempt(cleanIid, oppositeHint, true);
    if (result2.success) {
      console.log('[Puppeteer] Success on retry attempt!');
      return result2.cid;
    }

    // Both paths rejected it
    throw new CIDError('ACTIVATION_FAILED', `❌ *Activación rechazada por Microsoft*\nNo se pudo generar el Confirmation ID para este IID en ninguna de las rutas (Windows/Office).\n\nDetalles:\n${result2.errorText?.substring(0, 200) || 'Sin detalles'}`, { iid: cleanIid });

  } catch (err) {
    if (err instanceof CIDError) throw err;
    throw new CIDError('NETWORK_ERROR', `❌ *Error interno de automatización:* ${err.message}`, { iid: cleanIid });
  }
}

module.exports = { getCIDViaPuppeteer };
