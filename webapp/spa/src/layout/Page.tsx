import { useEffect } from "react";

/* 页头（标题/副标题/操作区）+ 内容容器；保持 --ph-h 与页头高度同步
 * （供 .filter-bar 吸顶使用）。 */
export function Page({
  title,
  subtitle,
  actions,
  children,
}: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  useEffect(() => {
    const ph = document.querySelector(".page-head") as HTMLElement | null;
    if (ph) {
      document.documentElement.style.setProperty("--ph-h", ph.offsetHeight + "px");
    }
  });

  return (
    <>
      <header className="page-head">
        <div>
          <h1>{title}</h1>
          {subtitle ? <p className="page-sub">{subtitle}</p> : null}
        </div>
        <div className="page-actions">{actions}</div>
      </header>
      <div className="content">{children}</div>
    </>
  );
}
