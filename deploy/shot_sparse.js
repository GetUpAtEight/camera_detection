/**
 * 截取低频巡检页面的实际界面，并顺带跑一遍「一键演示」验证交互链路。
 *
 * 用法: node deploy/shot_sparse.js [width] [height]
 * 依赖: playwright-core（用系统 Edge，无需下载 Chromium）
 *
 * 关键点：用 Chromium 的 fake media device 让 getUserMedia 在无摄像头环境也能出画面，
 * 否则「一键演示」拿不到底图。
 */
const path = require('path')
const { chromium } = require('playwright-core')

const EDGE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
const BASE = 'http://localhost:5173'
const OUT = path.resolve(__dirname, '../docs/screens')

const width = Number(process.argv[2] || 1680)
const height = Number(process.argv[3] || 2400)

async function main() {
  const browser = await chromium.launch({
    executablePath: EDGE,
    headless: true,
    args: [
      '--use-fake-ui-for-media-stream',
      '--use-fake-device-for-media-stream',
      '--autoplay-policy=no-user-gesture-required'
    ]
  })
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 })
  page.on('console', (msg) => {
    if (msg.type() === 'error') console.log('[console.error]', msg.text())
  })

  await page.goto(`${BASE}/sparse`, { waitUntil: 'networkidle', timeout: 60000 })
  await page.waitForTimeout(2000)

  // 等摄像头就绪（fake device 一般 1~3 秒）
  for (let i = 0; i < 15; i += 1) {
    const ready = await page.evaluate(() => {
      const v = document.querySelector('video')
      return !!v && v.readyState >= 2 && v.videoWidth > 0
    })
    if (ready) break
    await page.waitForTimeout(600)
  }

  // 用一个全新的机位，确保演示从零开始（基线清空由「一键演示」内部完成）
  const cameraId = `cam-demo-${Date.now().toString().slice(-5)}`
  const input = await page.$('input[placeholder="输入或选择机位 id"]')
  if (input) {
    await input.click()
    await page.keyboard.press('Control+A')
    await input.fill(cameraId)
    await page.keyboard.press('Enter')
    await page.waitForTimeout(600)
  }

  // 触发「一键演示」
  const demoBtn = await page.$('button:has-text("一键演示")')
  if (demoBtn) {
    await demoBtn.click()
    // 演示会提交 6 次抽样，等它跑完（按钮 loading 结束）
    for (let i = 0; i < 40; i += 1) {
      await page.waitForTimeout(700)
      const busy = await page.evaluate(() => {
        const btns = Array.from(document.querySelectorAll('button'))
        return btns.some((b) => b.textContent && b.textContent.includes('一键演示') && b.disabled)
      })
      if (!busy) break
    }
  }
  await page.waitForTimeout(1500)

  const file = path.join(OUT, 'sparse.png')
  await page.screenshot({ path: file, fullPage: true })
  console.log('saved', file)

  // ---- 第二张：参照物标定对话框 ----
  try {
    const lmBtn = await page.$('button:has-text("标定参照物")')
    if (lmBtn) {
      await lmBtn.click()
      await page.waitForTimeout(800)
      const capBtn = await page.$('button:has-text("取当前画面")')
      if (capBtn) {
        await capBtn.click()
        await page.waitForTimeout(1500)
      }
      const img = await page.$('.lm-img')
      if (img) {
        // 先清空已有标注，保证截图里只有这次画的两个框
        const clearBtn = await page.$('button:has-text("清空列表")')
        if (clearBtn) {
          await clearBtn.click().catch(() => {})
          await page.waitForTimeout(300)
        }
        const box = await img.boundingBox()
        if (box) {
          // 画两个框：右侧门、左下楼角
          const draw = async (fx, fy, fw, fh) => {
            const x0 = box.x + box.width * fx
            const y0 = box.y + box.height * fy
            await page.mouse.move(x0, y0)
            await page.mouse.down()
            await page.mouse.move(x0 + box.width * fw, y0 + box.height * fh, { steps: 8 })
            await page.mouse.up()
            await page.waitForTimeout(300)
          }
          await draw(0.06, 0.12, 0.20, 0.26)
          await draw(0.62, 0.18, 0.24, 0.24)
        }
      }
      await page.waitForTimeout(500)
      const dialog = await page.$('.el-dialog')
      const lmFile = path.join(OUT, 'sparse-landmark.png')
      if (dialog) {
        await dialog.screenshot({ path: lmFile })
      } else {
        await page.screenshot({ path: lmFile })
      }
      console.log('saved', lmFile)
    }
  } catch (e) {
    console.log('[landmark screenshot skipped]', e.message)
  }

  // 顺便把关键判定文本打印出来，便于确认演示结果
  const summary = await page.evaluate(() => {
    const title = document.querySelector('.verdict-title')?.textContent?.trim() || ''
    const sub = document.querySelector('.verdict-sub')?.textContent?.replace(/\s+/g, ' ').trim() || ''
    const reasons = Array.from(document.querySelectorAll('.reasons li')).map((li) => li.textContent.trim())
    const tl = Array.from(document.querySelectorAll('.tl-item')).map((el) => el.textContent.replace(/\s+/g, ' ').trim())
    return { title, sub, reasons, timeline: tl.slice(0, 8) }
  })
  console.log(JSON.stringify(summary, null, 2))

  await browser.close()
}

main().catch((e) => {
  console.error('FAILED', e)
  process.exit(1)
})
