const puppeteer = require('puppeteer');

async function testActivation() {
  console.log('Iniciando navegador...');
  const browser = await puppeteer.launch({
    headless: "new",
    args: ['--no-sandbox', '--disable-setuid-sandbox']
  });
  const page = await browser.newPage();
  await page.setUserAgent('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36');

  console.log('Navegando a /welcome...');
  await page.goto('https://visualsupport.microsoft.com/welcome', { waitUntil: 'networkidle2' });

  console.log('Haciendo clic en "Activate a Microsoft Product"...');
  const buttons = await page.$$('button');
  let activateBtn = null;
  for (const btn of buttons) {
    const text = await page.evaluate(el => el.textContent, btn);
    if (text.includes('Activate a Microsoft Product') || text.includes('Let’s Get Started')) {
      activateBtn = btn;
      break;
    }
  }

  if (activateBtn) {
    await Promise.all([
      page.waitForNavigation({ waitUntil: 'networkidle0' }),
      activateBtn.click()
    ]);
    
    await page.screenshot({ path: 'test_after_click.png' });
    const url = page.url();
    console.log('Nueva URL:', url);
    const body = await page.evaluate(() => document.body.innerText);
    console.log('Contenido:', body.substring(0, 300));
  } else {
    console.log('No encontré el botón.');
  }

  await browser.close();
}

testActivation().catch(console.error);
