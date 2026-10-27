/**
 * 实时监控页面的端到端截图：用 Chromium 的 fake 摄像头设备走完整交互。
 * 用法：node deploy/live_shot.js
 *
 * 依赖 playwright-core，它装在 frontend/node_modules 下（前端 e2e 用），
 * 而本脚本在 deploy/，Node 不会向上跨目录找到它，所以这里显式指路。
 */
const path = require('path')

function loadPlaywright() {
  const candidates = [
    'playwright-core',
    path.resolve(__dirname, '../frontend/node_modules/playwright-core'),
    // 本机把 playwright-core 装在 WorkBuddy 的 node 工作区里，一并兜底
    path.resolve('C:/Users/yzn/.workbuddy/binaries/node/workspace/node_modules/playwright-core')
  ]
  for (const name of candidates) {
    try {
      return require(name)
    } catch (e) {
      if (e.code !== 'MODULE_NOT_FOUND') throw e
    }
  }
  throw new Error('找不到 playwright-core：请先 cd frontend && npm i -D playwright-core')
}

const { chromium } = loadPlaywright()

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const BASE = 'http://localhost:5173'
const OUT = path.resolve(__dirname, '../docs/screens')

;(async () => {
  const browser = await chromium.launch({
    executablePath: EDGE,
    headless: true,
    args: [
      '--no-sandbox',
      '--disable-gpu',
      '--hide-scrollbars',
      // 提供一个虚拟摄像头并自动授权，headless 下才能跑通 getUserMedia
      '--use-fake-device-for-media-stream',
      '--use-fake-ui-for-media-stream',
      '--autoplay-policy=no-user-gesture-required'
    ]
  })
  const context = await browser.newContext({
    viewport: { width: 1680, height: 1500 },
    permissions: ['camera']
  })
  const page = await context.newPage()
  const logs = []
  page.on('console', (m) => logs.push(`[${m.type()}] ${m.text()}`))
  page.on('pageerror', (e) => logs.push(`[pageerror] ${e.message}`))
  page.on('response', (r) => {
    if (r.status() >= 400) logs.push(`[http ${r.status()}] ${r.url()}`)
  })

  await page.goto(`${BASE}/live`, { waitUntil: 'load', timeout: 60000 })
  await page.waitForTimeout(2500)

  // 开始监控
  const startBtn = page.getByRole('button', { name: /开始监控/ })
  await startBtn.click()
  console.log('[live] 已点击开始监控')

  // 学习期 20 帧 × 500ms ≈ 10s，先等进度条出现再等它消失
  await page.waitForSelector('.learn-overlay', { timeout: 20000 }).catch(() => {})
  // 学习期截图（体现"正在学习场景基线 N/M"）
  await page.waitForTimeout(3000)
  await page.screenshot({ path: path.join(OUT, 'live-learning.png'), fullPage: false })
  console.log('[shot] live-learning.png')

  await page.waitForSelector('.learn-overlay', { state: 'detached', timeout: 40000 }).catch(() => {})
  console.log('[live] 学习期结束')
  // 再等 3 个抓帧周期，让记录表里有数据
  await page.waitForTimeout(10000)

  const state = await page.evaluate(() => {
    const video = document.querySelector('video')
    const rows = document.querySelectorAll('.el-table__body tr')
    return {
      videoWidth: video?.videoWidth || 0,
      videoHeight: video?.videoHeight || 0,
      rows: rows.length,
      hasAlarm: !!document.querySelector('.preview-card.alarming'),
      trayText: document.querySelector('.alarm-title')?.textContent || ''
    }
  })
  console.log('[live] 页面状态', JSON.stringify(state))

  await page.screenshot({ path: path.join(OUT, 'live.png'), fullPage: false })
  console.log('[shot] live.png')

  // 停止
  const stopBtn = page.getByRole('button', { name: /停止监控/ })
  if (await stopBtn.count()) {
    await stopBtn.click()
    await page.waitForTimeout(1200)
  }

  const afterStop = await page.evaluate(() => ({
    placeholder: !!document.querySelector('.video-placeholder'),
    dotOn: !!document.querySelector('.preview-head .dot.on')
  }))
  console.log('[live] 停止后', JSON.stringify(afterStop))

  if (logs.length) {
    console.log('[console]')
    for (const line of logs.slice(-12)) console.log('  ', line)
  }

  await browser.close()
})()
