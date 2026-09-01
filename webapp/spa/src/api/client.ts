/* API 客户端：对齐旧 app.js 的 api() 语义——
 * JSON/FormData 自动处理、401 弹登录并重试一次、错误统一 throw Error(detail)。 */

export class ApiError extends Error {
  readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function isConflictError(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
}

type LoginPrompt = () => Promise<boolean>;

let loginPrompt: LoginPrompt | null = null;
export function setLoginPrompt(fn: LoginPrompt) {
  loginPrompt = fn;
}

/** 不触发登录弹窗的裸探测（启动时判断会话状态用） */
export async function probe(path: string): Promise<boolean> {
  try {
    const res = await fetch(path);
    return res.ok;
  } catch {
    return false;
  }
}

export async function api<T = unknown>(
  path: string,
  opts: { body?: unknown; method?: string } & Omit<RequestInit, "body"> = {},
): Promise<T> {
  const { body, ...rest } = opts;
  const send = () =>
    fetch(path, {
      ...rest,
      headers:
        body !== undefined && !(body instanceof FormData)
          ? { "Content-Type": "application/json", ...(rest.headers || {}) }
          : rest.headers,
      body:
        body === undefined
          ? undefined
          : body instanceof FormData
            ? body
            : JSON.stringify(body),
    });
  let res = await send();
  if (res.status === 401 && !path.startsWith("/api/login") && loginPrompt) {
    if (await loginPrompt()) res = await send();
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* 非 JSON 错误体 */
    }
    throw new ApiError(detail, res.status);
  }
  return (await res.json()) as T;
}
