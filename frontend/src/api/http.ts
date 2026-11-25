/** 统一请求封装：把后端的 {error:{code,message}} 转成可读异常，组件不直接 fetch。 */

export class ApiError extends Error {
  code: string
  detail: unknown

  constructor(code: string, message: string, detail?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.detail = detail
  }
}

const BASE = '/api'

type Body = Record<string, unknown> | undefined

async function request<T>(method: string, path: string, body?: Body): Promise<T> {
  const init: RequestInit = {
    method,
    headers: { 'Content-Type': 'application/json' },
  }
  if (body !== undefined) init.body = JSON.stringify(body)

  let resp: Response
  try {
    resp = await fetch(`${BASE}${path}`, init)
  } catch {
    throw new ApiError('NETWORK_ERROR', '无法连接后端服务，请确认后端已在 8000 端口启动')
  }

  if (resp.status === 204) return undefined as T

  const text = await resp.text()
  const payload = text ? safeParse(text) : null

  if (!resp.ok) {
    const err = (payload as { error?: { code?: string; message?: string; detail?: unknown } })?.error
    throw new ApiError(
      err?.code ?? `HTTP_${resp.status}`,
      err?.message ?? `请求失败（HTTP ${resp.status}）`,
      err?.detail,
    )
  }
  return payload as T
}

function safeParse(text: string): unknown {
  try {
    return JSON.parse(text)
  } catch {
    return { error: { code: 'BAD_RESPONSE', message: '后端返回了非 JSON 内容' } }
  }
}

export const http = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: Body) => request<T>('POST', path, body),
  put: <T>(path: string, body?: Body) => request<T>('PUT', path, body),
  del: <T>(path: string) => request<T>('DELETE', path),
}