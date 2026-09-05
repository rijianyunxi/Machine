import { useEffect, useRef, type RefObject } from "react";

// Shared stack: nested confirmations own Escape/focus without closing their parent.
const stack: HTMLElement[] = [];
let originalOverflow = "";
const selector = "button:not(:disabled), input:not(:disabled):not([type=hidden]), textarea:not(:disabled), select:not(:disabled), a[href], [tabindex]:not([tabindex='-1'])";

// Only siblings of the top dialog's ancestor chain are made inert. This also
// handles a confirmation rendered outside its parent modal without inerting it.
const inertNodes = new Map<HTMLElement, boolean>();
function syncInert() {
  inertNodes.forEach((value, node) => { node.inert = value; });
  inertNodes.clear();
  let active = stack[stack.length - 1];
  while (active && active !== document.body) {
    const parent = active.parentElement;
    if (!parent) break;
    for (const sibling of Array.from(parent.children)) {
      if (sibling !== active && sibling instanceof HTMLElement && sibling.id !== "toast-root") {
        inertNodes.set(sibling, sibling.inert);
        sibling.inert = true;
      }
    }
    active = parent;
  }
}

export function useDialog(
  ref: RefObject<HTMLElement>,
  open: boolean,
  onClose: () => void,
  initialFocus?: RefObject<HTMLElement>,
) {
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => {
    const box = ref.current;
    if (!open || !box) return;
    const previous = document.activeElement as HTMLElement | null;
    if (!stack.length) {
      originalOverflow = document.body.style.overflow;
      document.body.style.overflow = "hidden";
    }
    stack.push(box);
    syncInert();
    const top = () => stack[stack.length - 1] === box;
    const controls = () => Array.from(box.querySelectorAll<HTMLElement>(selector))
      .filter(el => el.tabIndex >= 0 && el.getClientRects().length && getComputedStyle(el).visibility !== "hidden");
    (initialFocus?.current || controls()[0] || box).focus();
    const onKey = (event: KeyboardEvent) => {
      if (!top() || event.defaultPrevented) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        close.current();
      } else if (event.key === "Tab") {
        const items = controls();
        const first = items[0], last = items[items.length - 1];
        if (!items.length) { event.preventDefault(); box.focus(); }
        else if (event.shiftKey && (document.activeElement === first || document.activeElement === box)) {
          event.preventDefault(); last.focus();
        } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === box)) {
          event.preventDefault(); first.focus();
        }
      }
    };
    const onFocus = (event: FocusEvent) => {
      if (top() && !box.contains(event.target as Node)) (controls()[0] || box).focus();
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("focusin", onFocus);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("focusin", onFocus);
      const wasTop = top();
      const index = stack.indexOf(box);
      if (index !== -1) stack.splice(index, 1);
      syncInert();
      if (!stack.length) document.body.style.overflow = originalOverflow;
      if (wasTop && previous?.isConnected) previous.focus();
    };
  }, [ref, open, initialFocus]);
}
