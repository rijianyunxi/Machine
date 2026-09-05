import {
  Children,
  Fragment,
  forwardRef,
  isValidElement,
  useEffect,
  useId,
  useImperativeHandle,
  useMemo,
  useRef,
  useState,
} from "react";
import type { CSSProperties, ReactNode } from "react";
import { Icon } from "../layout/icons";

export type SelectChangeEvent = {
  target: { value: string };
  currentTarget: { value: string };
};

export type SelectHandle = {
  readonly value: string;
  focus: () => void;
};

type SelectProps = {
  value?: string | number;
  defaultValue?: string | number;
  onChange?: (event: SelectChangeEvent) => void;
  children?: ReactNode;
  className?: string;
  style?: CSSProperties;
  disabled?: boolean;
  id?: string;
  title?: string;
  name?: string;
  required?: boolean;
  placeholder?: ReactNode;
  "aria-label"?: string;
  "aria-labelledby"?: string;
  "aria-describedby"?: string;
};

type SelectOption = {
  key: string;
  value: string;
  label: ReactNode;
  disabled: boolean;
};

type OptionProps = {
  value?: string | number;
  disabled?: boolean;
  children?: ReactNode;
};

function collectOptions(children: ReactNode, result: SelectOption[] = []): SelectOption[] {
  Children.forEach(children, (child) => {
    if (!isValidElement(child)) return;
    if (child.type === Fragment) {
      collectOptions((child.props as { children?: ReactNode }).children, result);
      return;
    }
    if (typeof child.type !== "string" || child.type !== "option") return;
    const props = child.props as OptionProps;
    const value = String(props.value ?? "");
    result.push({
      key: String(child.key ?? result.length),
      value,
      label: props.children ?? value,
      disabled: !!props.disabled,
    });
  });
  return result;
}

function firstEnabled(options: SelectOption[], from = 0, step = 1): number {
  if (!options.length) return -1;
  let index = from;
  for (let count = 0; count < options.length; count += 1) {
    if (!options[index]?.disabled) return index;
    index = (index + step + options.length) % options.length;
  }
  return -1;
}

export const Select = forwardRef<SelectHandle, SelectProps>(function Select(
  {
    value,
    defaultValue,
    onChange,
    children,
    className,
    style,
    disabled = false,
    id,
    title,
    name,
    required,
    placeholder = "请选择",
    "aria-label": ariaLabel,
    "aria-labelledby": ariaLabelledBy,
    "aria-describedby": ariaDescribedBy,
  },
  ref,
) {
  const options = useMemo(() => collectOptions(children), [children]);
  const isControlled = value !== undefined;
  const [internalValue, setInternalValue] = useState(
    String(defaultValue ?? options[0]?.value ?? ""),
  );
  const currentValue = isControlled ? String(value ?? "") : internalValue;

  useEffect(() => {
    if (isControlled || !options.length) return;
    if (options.some((option) => option.value === internalValue)) return;
    setInternalValue(options[firstEnabled(options)]?.value ?? "");
  }, [internalValue, isControlled, options]);
  const selectedIndex = options.findIndex((option) => option.value === currentValue);
  const selectedOption = selectedIndex >= 0 ? options[selectedIndex] : undefined;
  const [open, setOpen] = useState(false);
  const [highlightedIndex, setHighlightedIndex] = useState(
    selectedIndex >= 0 ? selectedIndex : firstEnabled(options),
  );
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const optionRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const listId = useId();

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [open]);

  useEffect(() => {
    if (!open || highlightedIndex < 0) return;
    optionRefs.current[highlightedIndex]?.scrollIntoView({ block: "nearest" });
  }, [open, highlightedIndex]);

  useEffect(() => {
    if (!open) return;
    const next = selectedIndex >= 0 ? selectedIndex : firstEnabled(options);
    setHighlightedIndex(next);
  }, [open, options, selectedIndex]);

  useImperativeHandle(
    ref,
    () => ({
      get value() {
        return currentValue;
      },
      focus: () => triggerRef.current?.focus(),
    }),
    [currentValue],
  );

  const closeMenu = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  const selectValue = (nextValue: string) => {
    if (disabled) return;
    if (!isControlled) setInternalValue(nextValue);
    onChange?.({ target: { value: nextValue }, currentTarget: { value: nextValue } });
    closeMenu();
  };

  const moveHighlight = (step: number) => {
    const start = highlightedIndex >= 0 ? highlightedIndex : firstEnabled(options);
    if (start < 0) return;
    setHighlightedIndex(firstEnabled(options, (start + step + options.length) % options.length, step));
  };

  const openMenu = () => {
    if (disabled) return;
    setHighlightedIndex(selectedIndex >= 0 ? selectedIndex : firstEnabled(options));
    setOpen(true);
  };

  return (
    <div
      ref={rootRef}
      className={"select-control" + (className ? " " + className : "")}
      style={style}
      data-disabled={disabled ? "true" : undefined}
      data-required={required ? "true" : undefined}
      data-name={name || undefined}
    >
      <button
        id={id}
        ref={triggerRef}
        type="button"
        className="select-trigger"
        disabled={disabled}
        title={title}
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={open ? listId : undefined}
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledBy}
        aria-describedby={ariaDescribedBy}
        onClick={() => (open ? closeMenu() : openMenu())}
        onKeyDown={(event) => {
          if (event.key === "Escape") {
            if (open) {
              event.preventDefault();
              closeMenu();
            }
            return;
          }
          if (event.key === "ArrowDown" || event.key === "ArrowUp") {
            event.preventDefault();
            if (!open) {
              openMenu();
            } else {
              moveHighlight(event.key === "ArrowDown" ? 1 : -1);
            }
            return;
          }
          if (event.key === "Home" || event.key === "End") {
            if (!open) return;
            event.preventDefault();
            const index = event.key === "Home" ? firstEnabled(options) : firstEnabled(options, options.length - 1, -1);
            setHighlightedIndex(index);
            return;
          }
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault();
            if (!open) {
              openMenu();
            } else if (highlightedIndex >= 0 && !options[highlightedIndex]?.disabled) {
              selectValue(options[highlightedIndex].value);
            }
          }
        }}
      >
        <span className="select-value">
          {selectedOption?.label ?? (currentValue ? currentValue : placeholder)}
        </span>
        <Icon name="chevron-down" size={14} />
      </button>
      {open && options.length ? (
        <div id={listId} className="select-options" role="listbox" aria-label={ariaLabel}>
          {options.map((option, index) => (
            <button
              key={option.key}
              ref={(element) => { optionRefs.current[index] = element; }}
              type="button"
              className={"select-option" + (index === highlightedIndex ? " highlighted" : "") + (index === selectedIndex ? " selected" : "")}
              role="option"
              aria-selected={index === selectedIndex}
              disabled={option.disabled}
              onMouseEnter={() => { if (!option.disabled) setHighlightedIndex(index); }}
              onMouseDown={(event) => event.preventDefault()}
              onClick={() => selectValue(option.value)}
            >
              <span className="select-option-label">{option.label}</span>
              {index === selectedIndex ? <Icon name="check" size={13} /> : null}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
});

Select.displayName = "Select";
