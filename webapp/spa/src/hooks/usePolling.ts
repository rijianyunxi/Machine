import { useEffect, useRef } from "react";

/* 立即执行一次 + 定时轮询；ms 毫秒。fn 通过 ref 保持最新，无需担心闭包过期。 */
export function usePolling(
  fn: () => void | Promise<void>,
  ms: number,
  deps: unknown[] = [],
) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    let alive = true;
    const run = async () => {
      if (!alive) return;
      try {
        await ref.current();
      } catch (e) {
        console.error(e);
      }
    };
    run();
    const timer = setInterval(run, ms);
    return () => {
      alive = false;
      clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
