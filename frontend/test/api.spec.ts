import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { AxiosAdapter, InternalAxiosRequestConfig } from 'axios'

// element-plus 的 ElMessage 在 node 环境里会碰 DOM，测试里只需要知道「弹了什么」。
const messages: Array<{ type: string; text: string }> = []
vi.mock('element-plus', () => ({
  ElMessage: {
    error: (text: string) => messages.push({ type: 'error', text }),
    success: (text: string) => messages.push({ type: 'success', text }),
    warning: (text: string) => messages.push({ type: 'warning', text }),
    info: (text: string) => messages.push({ type: 'info', text })
  }
}))

import http, {
  ApiError,
  ApiTimeoutError,
  DEFAULT_TIMEOUT_MS,
  REQUEST_TIMEOUTS,
  __resetServerClock,
  getLastServerNow,
  serverNow,
  sparseApi,
  toApiMessage
} from '../src/api'

/** 用一次性 adapter 替换真实网络：直接返回构造好的响应。 */
function respondOnce(body: unknown, headers: Record<string, string> = {}) {
  const adapter: AxiosAdapter = async (config: InternalAxiosRequestConfig) => ({
    data: body,
    status: 200,
    statusText: 'OK',
    headers: { ...headers },
    config
  })
  http.defaults.adapter = adapter as never
}

function failOnce(error: unknown) {
  http.defaults.adapter = (async () => {
    throw error
  }) as never
}

beforeEach(() => {
  messages.length = 0
  __resetServerClock()
})

describe('响应拦截器：{code, message, data} 解包', () => {
  it('code === 0 时把 data 直接交给调用方', async () => {
    respondOnce({ code: 0, message: 'ok', data: { cameras: [{ cameraId: 'cam-1', samples: 3 }] } })
    const res = await sparseApi.cameras()
    expect(res).toEqual({ cameras: [{ cameraId: 'cam-1', samples: 3 }] })
    expect(messages).toHaveLength(0)
  })

  it('code !== 0 时抛错、弹提示，并且带上业务 code', async () => {
    respondOnce({ code: 4001, message: '机位不存在', data: null })
    await expect(sparseApi.state('cam-x')).rejects.toBeInstanceOf(ApiError)
    await expect(sparseApi.state('cam-x')).rejects.toMatchObject({ code: 4001 })
    expect(messages.at(-1)).toEqual({ type: 'error', text: '机位不存在' })
  })

  it('没有 code 字段的响应原样返回（健康检查这类直出结构）', async () => {
    respondOnce({ online: true, baseUrl: 'http://127.0.0.1:8090' })
    const res = await sparseApi.cameras()
    expect(res).toEqual({ online: true, baseUrl: 'http://127.0.0.1:8090' })
  })
})

describe('超时', () => {
  it('默认实例有 15s 超时，sample 保持 120s', () => {
    expect(http.defaults.timeout).toBe(DEFAULT_TIMEOUT_MS)
    expect(DEFAULT_TIMEOUT_MS).toBe(15000)
    expect(REQUEST_TIMEOUTS.sample).toBe(120000)
    expect(REQUEST_TIMEOUTS.calibration).toBe(60000)
  })

  it('axios 的 ECONNABORTED 被翻译成可读的超时提示', async () => {
    const err = Object.assign(new Error('timeout of 15000ms exceeded'), {
      isAxiosError: true,
      code: 'ECONNABORTED',
      config: { timeout: 15000, url: '/sparse/meta' }
    })
    failOnce(err)
    await expect(sparseApi.meta()).rejects.toMatchObject({
      message: '请求超时（15 秒未响应）'
    })
    expect(messages.at(-1)?.text).toBe('请求超时（15 秒未响应）')
  })

  it('502 给出算法层未启动的可读提示', () => {
    const err = Object.assign(new Error('Request failed with status code 502'), {
      isAxiosError: true,
      response: { status: 502, data: {} }
    })
    expect(toApiMessage(err)).toBe('算法服务不可用，请确认 Python 算法层已启动')
  })

  it('错误对象为空时仍返回可读文案，而不是 undefined', () => {
    expect(toApiMessage(undefined)).toBe('网络异常')
    expect(new ApiTimeoutError(15000, '/x').message).toContain('15 秒')
  })
})

describe('服务端时间（X-Server-Now）', () => {
  it('响应头里的服务端时间被记下来，并可持续外推', async () => {
    expect(getLastServerNow()).toBe(0)
    respondOnce({ code: 0, data: { cameras: [] } }, { 'x-server-now': '1735689600000' })
    await sparseApi.cameras()
    expect(getLastServerNow()).toBe(1735689600000)
    const now = serverNow()
    expect(now).toBeGreaterThanOrEqual(1735689600000)
    expect(now).toBeLessThan(1735689600000 + 60_000)
  })

  it('错误响应也回填服务端时间（算法层 500 时前端时钟仍可信）', async () => {
    failOnce(
      Object.assign(new Error('boom'), {
        isAxiosError: true,
        response: { status: 500, data: { message: '算法层挂了' }, headers: { 'x-server-now': '1700000000000' } }
      })
    )
    await expect(sparseApi.meta()).rejects.toBeTruthy()
    expect(getLastServerNow()).toBe(1700000000000)
  })

  it('缺少该头时不会污染已知的服务端时间', async () => {
    respondOnce({ code: 0, data: {} }, { 'x-server-now': '1735689600000' })
    await sparseApi.meta()
    respondOnce({ code: 0, data: {} })
    await sparseApi.meta()
    expect(getLastServerNow()).toBe(1735689600000)
  })
})
