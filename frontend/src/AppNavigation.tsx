import {
  Archive,
  CalendarDays,
  ChevronDown,
  FileSpreadsheet,
  MapPinned,
  Users,
} from "lucide-react";
import {
  KeyboardEvent,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { useLanguage } from "./i18n";

export type AppTab = "workspace" | "daily-payables" | "mexico-tracking" | "archive" | "admin";

export type AppNavigationProps = {
  tab: AppTab;
  canAdmin: boolean;
  canMexico: boolean;
  onSelect: (tab: AppTab) => void;
};

type NavigationItem = {
  tab: AppTab;
  label: string;
  icon: typeof FileSpreadsheet;
};

export function AppNavigation({ tab, canAdmin, canMexico, onSelect }: AppNavigationProps) {
  const { t } = useLanguage();
  const items = useMemo<NavigationItem[]>(() => {
    const available: NavigationItem[] = [
      { tab: "workspace", label: t("工作台", "Panel de trabajo"), icon: FileSpreadsheet },
      { tab: "daily-payables", label: t("每日应付", "Pagos diarios pendientes"), icon: CalendarDays },
    ];
    if (canMexico) available.push({ tab: "mexico-tracking", label: t("墨西哥审批", "Aprobaciones de México"), icon: MapPinned });
    available.push({ tab: "archive", label: t("归档", "Archivo"), icon: Archive });
    if (canAdmin) available.push({ tab: "admin", label: t("管理", "Administración"), icon: Users });
    return available;
  }, [canAdmin, canMexico, t]);
  const rootRef = useRef<HTMLElement | null>(null);
  const measureRef = useRef<HTMLDivElement | null>(null);
  const measureItemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const moreMeasureRef = useRef<HTMLButtonElement | null>(null);
  const moreWrapRef = useRef<HTMLDivElement | null>(null);
  const menuItemRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [visibleCount, setVisibleCount] = useState(items.length);
  const [menuOpen, setMenuOpen] = useState(false);

  useLayoutEffect(() => {
    const root = rootRef.current;
    const measure = measureRef.current;
    if (!root || !measure) return;

    const calculate = () => {
      const available = root.clientWidth || 0;
      const widths = items.map((_, index) => measureItemRefs.current[index]?.offsetWidth || 0);
      const gap = Number.parseFloat(window.getComputedStyle(measure).columnGap || "0") || 0;
      const allWidth = widths.reduce((sum, width) => sum + width, 0)
        + Math.max(0, widths.length - 1) * gap;
      if (allWidth <= available) {
        setVisibleCount(widths.length);
        setMenuOpen(false);
        return;
      }
      let used = moreMeasureRef.current?.offsetWidth || 0;
      let count = 0;
      for (const width of widths) {
        const next = used + gap + width;
        if (next > available) break;
        used = next;
        count += 1;
      }
      setVisibleCount(Math.min(count, widths.length - 1));
    };

    calculate();
    const observer = new ResizeObserver(calculate);
    observer.observe(root);
    return () => observer.disconnect();
  }, [items]);

  useEffect(() => {
    if (!menuOpen) return;
    const closeOutside = (event: PointerEvent) => {
      if (!moreWrapRef.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    const closeOnEscape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [menuOpen]);

  const visibleItems = items.slice(0, visibleCount);
  const overflowItems = items.slice(visibleCount);
  const selectedInOverflow = overflowItems.some((item) => item.tab === tab);

  function select(next: AppTab) {
    setMenuOpen(false);
    onSelect(next);
  }

  function moveMenuFocus(event: KeyboardEvent<HTMLButtonElement>, index: number) {
    if (!overflowItems.length) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      const direction = event.key === "ArrowDown" ? 1 : -1;
      const next = (index + direction + overflowItems.length) % overflowItems.length;
      menuItemRefs.current[next]?.focus();
    }
  }

  return (
    <nav className="app-nav" ref={rootRef} aria-label={t("主导航", "Navegación principal")}>
      <div className="app-nav-list">
        {visibleItems.map((item) => {
          const Icon = item.icon;
          return (
            <button
              key={item.tab}
              data-page={item.tab === "mexico-tracking" ? "mexico-tracking" : undefined}
              className={tab === item.tab ? "active" : ""}
              onClick={() => select(item.tab)}
            >
              <Icon size={16} />
              {item.label}
            </button>
          );
        })}
        {overflowItems.length > 0 && (
          <div className="app-nav-more-wrap" ref={moreWrapRef}>
            <button
              className={`app-nav-more ${selectedInOverflow ? "active" : ""}`}
              type="button"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              onClick={() => setMenuOpen((open) => !open)}
              onKeyDown={(event) => {
                if (event.key !== "ArrowDown") return;
                event.preventDefault();
                setMenuOpen(true);
                window.queueMicrotask(() => menuItemRefs.current[0]?.focus());
              }}
            >
              {t("更多", "Más")}
              <ChevronDown size={15} />
            </button>
            {menuOpen && (
              <div className="app-nav-menu" role="menu">
                {overflowItems.map((item, index) => {
                  const Icon = item.icon;
                  return (
                    <button
                      key={item.tab}
                      ref={(node) => { menuItemRefs.current[index] = node; }}
                      role="menuitem"
                      className={tab === item.tab ? "active" : ""}
                      onClick={() => select(item.tab)}
                      onKeyDown={(event) => moveMenuFocus(event, index)}
                    >
                      <Icon size={16} />
                      {item.label}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </div>

      <div className="app-nav-measure" ref={measureRef} aria-hidden="true">
        {items.map((item, index) => {
          const Icon = item.icon;
          return (
            <button
              key={item.tab}
              ref={(node) => { measureItemRefs.current[index] = node; }}
              tabIndex={-1}
            >
              <Icon size={16} />
              {item.label}
            </button>
          );
        })}
        <button className="app-nav-more" ref={moreMeasureRef} tabIndex={-1}>
          {t("更多", "Más")}
          <ChevronDown size={15} />
        </button>
      </div>
    </nav>
  );
}
