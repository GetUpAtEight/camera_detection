/**
 * 用本机 Edge 截取 UI 图（无需下载 Chromium）
 * 用法：node deploy/shot.js <taskId> [width] [height]
 * 依赖：playwright-core（装在受管 node workspace 里），通过 NODE_PATH 引用
 * 注意：页面主体是 el-main 内部滚动，fullPage 抓不到下方内容，所以直接把视口调高。
 */
const path = require('path')
const { chromium } = require('playwright-core')

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const BASE = 'http://localhost:5173'
const OUT = path.resolve(__dirname, '../docs/screens')
const taskId = process.argv[2] || ''
const width = Number(process.argv[3] || 1680)
const height = Number(process.argv[4] || 1600)

const q = taskId ? `?taskId=${taskId}` : ''
const pages = [
  { url: `${BASE}/detect${q}`, file: 'detect.png', height: Math.max(height, 1800), click: '按标注分段' },
  { url: `${BASE}/analysis${q}`, file: 'analysis.png', height },
  { url: `${BASE}/compare${q}`, file: 'compare.png', height: Math.min(height, 1400) },
  { url: `${BASE}/upload`, file: 'upload.png', height: 1100 }
]

;(async () => {
  const browser = await chromium.launch({
    executablePath: EDGE,
    headless: true,
    args: ['--no-sandbox', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=1']
  })
  for (const item of pages) {
    const context = await browser.newContext({ viewport: { width, height: item.height } })
    const page = await context.newPage()
    try {
      await page.goto(item.url, { waitUntil: 'load', timeout: 60000 })
      await page.waitForTimeout(3000)
      if (item.click) {
        try {
          await page.locator('.el-radio-button', { hasText: item.click }).first().click({ timeout: 5000 })
          await page.waitForTimeout(800)
        } catch (e) {
          console.warn('[shot] 点击', item.click, '失败：', e.message)
        }
      }
      await page.waitForTimeout(600)
      await page.screenshot({ path: path.join(OUT, item.file) })
      console.log('[shot]', item.file, `${width}x${item.height}`)
    } catch (e) {
      console.error('[shot] failed', item.file, e.message)
    }
    await context.close()
  }
  await browser.close()
})()

