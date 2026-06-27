import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type MouseEvent as ReactMouseEvent,
  type ReactNode,
} from "react";
import { useMessages } from "@/locales";
import styles from "./RuleTable.module.css";

export interface RuleTableColumnEdit<T> {
  getValue: (item: T) => string;
  onCommit: (index: number, value: string) => void;
  /** Render a textarea instead of an input. */
  multiline?: boolean;
  placeholder?: string;
}

export interface RuleTableColumn<T> {
  key: string;
  label: string;
  width: string;
  align?: "left" | "right" | "center";
  render: (item: T, index: number) => ReactNode;
  /** Enable inline edit on double-click for this column. */
  edit?: RuleTableColumnEdit<T>;
  /** When set, the column header is clickable and toggles sort
   * ``asc`` → ``desc`` → ``null`` on the parent's ``onSortChange``
   * callback. The parent owns the actual sort (re-orders ``rules``
   * before passing in) so the table stays generic. */
  sortable?: boolean;
}

export type SortDirection = "asc" | "desc";

export interface SortState {
  key: string;
  direction: SortDirection;
}

export interface RuleTableAction {
  label: string;
  onClick: () => void;
  primary?: boolean;
  disabled?: boolean;
}

export interface RuleTableSelection {
  indices: number[];
  last: number | null;
}

export const EMPTY_SELECTION: RuleTableSelection = { indices: [], last: null };

interface RuleTableProps<T> {
  rules: T[];
  selection: RuleTableSelection;
  onSelectionChange: (next: RuleTableSelection) => void;
  isEnabled: (rule: T) => boolean;
  columns: RuleTableColumn<T>[];
  emptyMessage: ReactNode;
  editor?: ReactNode;
  toolbar?: RuleTableAction[];
  /** Right-click → "Delete N rules"; called with the rows the user
   *  acted on. When omitted, the context menu is suppressed. */
  onBulkDelete?: (indices: number[]) => void;
  /** Right-click → "Duplicate N rules"; appends fresh copies after
   * the current list (parent owns the actual append logic). */
  onBulkDuplicate?: (indices: number[]) => void;
  /** Localized labels for the row context menu. */
  contextMenuLabels?: {
    deleteSelected: (n: number) => string;
    duplicateSelected?: (n: number) => string;
  };
  /** Active sort state. When set, the matching column shows an
   * indicator. Owned by the parent. */
  sortState?: SortState | null;
  /** Fired when a sortable header is clicked. ``next`` is ``null``
   * when the user clicks through to clear the sort. */
  onSortChange?: (next: SortState | null) => void;
  getRowClassName?: (rule: T, index: number) => string | undefined;
}

interface ContextMenuState {
  x: number;
  y: number;
  /** The row indices the menu's actions should affect. */
  targetIndices: number[];
}

interface EditingCell {
  rowIndex: number;
  colKey: string;
}

function rangeOf(a: number, b: number): number[] {
  const [lo, hi] = a < b ? [a, b] : [b, a];
  const out: number[] = [];
  for (let i = lo; i <= hi; i++) out.push(i);
  return out;
}

function isModifierClick(event: ReactMouseEvent): boolean {
  return event.metaKey || event.ctrlKey;
}

export function RuleTable<T>({
  rules,
  selection,
  onSelectionChange,
  isEnabled,
  columns,
  emptyMessage,
  editor,
  toolbar = [],
  onBulkDelete,
  onBulkDuplicate,
  contextMenuLabels,
  sortState,
  onSortChange,
  getRowClassName,
}: RuleTableProps<T>) {
  const messages = useMessages();
  const gridTemplate = ["28px", "36px", ...columns.map((c) => c.width)].join(
    " ",
  );
  const tableRef = useRef<HTMLDivElement>(null);
  const headerCheckboxRef = useRef<HTMLInputElement>(null);
  const [contextMenu, setContextMenu] = useState<ContextMenuState | null>(null);
  const [editing, setEditing] = useState<EditingCell | null>(null);

  const selectedSet = useMemo(
    () => new Set(selection.indices),
    [selection.indices],
  );

  const allChecked =
    rules.length > 0 && selection.indices.length === rules.length;
  const someChecked =
    selection.indices.length > 0 && selection.indices.length < rules.length;

  // <input type="checkbox"> doesn't expose ``indeterminate`` as an
  // attribute — it's a DOM property. Sync via ref.
  useEffect(() => {
    if (headerCheckboxRef.current) {
      headerCheckboxRef.current.indeterminate = someChecked;
    }
  }, [someChecked]);

  const toggleAll = () => {
    if (allChecked || someChecked) {
      setSelection([], null);
    } else {
      const all = rules.map((_, i) => i);
      setSelection(all, all[all.length - 1] ?? null);
    }
  };

  const toggleRow = (index: number) => {
    const next = selectedSet.has(index)
      ? selection.indices.filter((i) => i !== index)
      : [...selection.indices, index];
    setSelection(next, index);
  };

  const setSelection = useCallback(
    (indices: number[], last: number | null) => {
      const sorted = Array.from(new Set(indices)).sort((a, b) => a - b);
      onSelectionChange({ indices: sorted, last });
    },
    [onSelectionChange],
  );

  const handleRowClick = (event: ReactMouseEvent, index: number) => {
    if (event.shiftKey && selection.last !== null) {
      const range = rangeOf(selection.last, index);
      const next = isModifierClick(event)
        ? Array.from(new Set([...selection.indices, ...range]))
        : range;
      setSelection(next, index);
      return;
    }
    if (isModifierClick(event)) {
      const next = selectedSet.has(index)
        ? selection.indices.filter((i) => i !== index)
        : [...selection.indices, index];
      setSelection(next, index);
      return;
    }
    // Plain click: replace selection with this row.
    setSelection([index], index);
  };

  const handleRowContextMenu = (event: ReactMouseEvent, index: number) => {
    if (!onBulkDelete) return;
    event.preventDefault();
    // Right-click target rules:
    // - If clicked row is in current selection, act on whole selection.
    // - Else replace selection with the clicked row only.
    let targets = selection.indices;
    if (!selectedSet.has(index)) {
      targets = [index];
      setSelection([index], index);
    }
    setContextMenu({
      x: event.clientX,
      y: event.clientY,
      targetIndices: targets,
    });
  };

  const closeContextMenu = useCallback(() => setContextMenu(null), []);

  const handleBulkDelete = () => {
    if (contextMenu && onBulkDelete) {
      onBulkDelete(contextMenu.targetIndices);
      setSelection([], null);
    }
    closeContextMenu();
  };

  const handleBulkDuplicate = () => {
    if (contextMenu && onBulkDuplicate) {
      onBulkDuplicate(contextMenu.targetIndices);
    }
    closeContextMenu();
  };

  const handleHeaderClick = (col: RuleTableColumn<T>) => {
    if (!col.sortable || !onSortChange) return;
    if (sortState?.key !== col.key) {
      onSortChange({ key: col.key, direction: "asc" });
      return;
    }
    if (sortState.direction === "asc") {
      onSortChange({ key: col.key, direction: "desc" });
      return;
    }
    // desc → off
    onSortChange(null);
  };

  // Cmd/Ctrl+A: select all rows when the table region has focus.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (
        (event.metaKey || event.ctrlKey) &&
        event.key.toLowerCase() === "a" &&
        tableRef.current?.contains(document.activeElement)
      ) {
        if (rules.length === 0) return;
        event.preventDefault();
        const all = rules.map((_, i) => i);
        setSelection(all, all[all.length - 1]);
      }
      if (event.key === "Escape") {
        if (editing) setEditing(null);
        if (contextMenu) closeContextMenu();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [rules.length, setSelection, editing, contextMenu, closeContextMenu]);

  // Click-outside to close the context menu.
  useEffect(() => {
    if (!contextMenu) return;
    const onDown = () => closeContextMenu();
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [contextMenu, closeContextMenu]);

  const enterEdit = (rowIndex: number, colKey: string) => {
    const col = columns.find((c) => c.key === colKey);
    if (!col || !col.edit) return;
    setEditing({ rowIndex, colKey });
  };

  const renderEditor = (rowIndex: number, col: RuleTableColumn<T>) => {
    if (!col.edit) return null;
    const initial = col.edit.getValue(rules[rowIndex]);
    const commit = (next: string) => {
      if (next !== initial) col.edit!.onCommit(rowIndex, next);
      setEditing(null);
    };
    return (
      <InlineEditor
        initial={initial}
        multiline={col.edit.multiline}
        placeholder={col.edit.placeholder}
        onCommit={commit}
        onCancel={() => setEditing(null)}
      />
    );
  };

  const hasSidebar = toolbar.length > 0 || editor != null;

  return (
    <div className={styles.editorGrid}>
      <div ref={tableRef} className={styles.tableWrap} tabIndex={0} role="grid">
        <div
          className={styles.tableHeader}
          style={{ gridTemplateColumns: gridTemplate }}
        >
          <span className={styles.checkboxCell}>
            <input
              ref={headerCheckboxRef}
              type="checkbox"
              className={styles.checkbox}
              checked={allChecked}
              onChange={toggleAll}
              disabled={rules.length === 0}
              aria-label={messages.ruleTable.selectAll}
            />
          </span>
          <span className={styles.colIndex}>#</span>
          {columns.map((col) => {
            const isSorted = sortState?.key === col.key;
            const indicator = !isSorted
              ? ""
              : sortState!.direction === "asc"
                ? " ↑"
                : " ↓";
            const headerClass = [
              col.align === "right" ? styles.colRight : "",
              col.sortable ? styles.headerSortable : "",
              isSorted ? styles.headerSortActive : "",
            ]
              .filter(Boolean)
              .join(" ");
            return (
              <span
                key={col.key}
                className={headerClass}
                onClick={
                  col.sortable ? () => handleHeaderClick(col) : undefined
                }
                role={col.sortable ? "button" : undefined}
                tabIndex={col.sortable ? 0 : undefined}
              >
                {col.label}
                {indicator}
              </span>
            );
          })}
        </div>
        {rules.length === 0 ? (
          <div className={styles.empty}>{emptyMessage}</div>
        ) : (
          rules.map((rule, index) => {
            const inSelection = selectedSet.has(index);
            const isPrimary = selection.last === index && inSelection;
            const enabled = isEnabled(rule);
            const rowClass = [
              styles.row,
              isPrimary ? styles.rowActive : "",
              inSelection && !isPrimary ? styles.rowMultiSelected : "",
              enabled ? "" : styles.rowDisabled,
              getRowClassName?.(rule, index) ?? "",
            ]
              .filter(Boolean)
              .join(" ");
            return (
              <div
                key={index}
                role="row"
                aria-selected={inSelection}
                className={rowClass}
                style={{ gridTemplateColumns: gridTemplate }}
                onClick={(event) => handleRowClick(event, index)}
                onContextMenu={(event) => handleRowContextMenu(event, index)}
              >
                <span
                  className={styles.checkboxCell}
                  onClick={(event) => event.stopPropagation()}
                >
                  <input
                    type="checkbox"
                    className={styles.checkbox}
                    checked={inSelection}
                    onChange={() => toggleRow(index)}
                    aria-label={`select row ${index + 1}`}
                  />
                </span>
                <span className={`${styles.colIndex} tnum`}>{index + 1}</span>
                {columns.map((col) => {
                  const editable = col.edit !== undefined;
                  const isEditing =
                    editing?.rowIndex === index && editing?.colKey === col.key;
                  const cellClass = [
                    styles.cell,
                    col.align === "right" ? styles.colRight : "",
                    editable && !isEditing ? styles.cellEditable : "",
                  ]
                    .filter(Boolean)
                    .join(" ");
                  return (
                    <span
                      key={col.key}
                      className={cellClass}
                      onDoubleClick={
                        editable
                          ? (event) => {
                              event.stopPropagation();
                              enterEdit(index, col.key);
                            }
                          : undefined
                      }
                    >
                      {isEditing
                        ? renderEditor(index, col)
                        : col.render(rule, index)}
                    </span>
                  );
                })}
              </div>
            );
          })
        )}
      </div>

      {hasSidebar ? (
        <aside className={styles.sidebar}>
          <div className={styles.toolbar}>
            {toolbar.map((action) => (
              <button
                key={action.label}
                type="button"
                className={`${styles.toolbarBtn} ${action.primary ? styles.toolbarBtnPrimary : ""}`.trim()}
                onClick={action.onClick}
                disabled={action.disabled}
              >
                {action.primary ? "+ " : ""}
                {action.label}
              </button>
            ))}
          </div>
          <div className={styles.editorBlock}>{editor}</div>
        </aside>
      ) : null}

      {contextMenu ? (
        <div
          className={styles.contextMenu}
          style={{ top: contextMenu.y, left: contextMenu.x }}
          onMouseDown={(event) => event.stopPropagation()}
          role="menu"
        >
          {onBulkDuplicate ? (
            <button
              type="button"
              className={styles.contextMenuItem}
              onClick={handleBulkDuplicate}
            >
              {contextMenuLabels?.duplicateSelected?.(
                contextMenu.targetIndices.length,
              ) ?? `Duplicate ${contextMenu.targetIndices.length}`}
            </button>
          ) : null}
          <button
            type="button"
            className={styles.contextMenuItem}
            onClick={handleBulkDelete}
          >
            {contextMenuLabels?.deleteSelected(
              contextMenu.targetIndices.length,
            ) ?? `Delete ${contextMenu.targetIndices.length}`}
          </button>
        </div>
      ) : null}
    </div>
  );
}

interface InlineEditorProps {
  initial: string;
  multiline?: boolean;
  placeholder?: string;
  onCommit: (next: string) => void;
  onCancel: () => void;
}

function InlineEditor({
  initial,
  multiline,
  placeholder,
  onCommit,
  onCancel,
}: InlineEditorProps) {
  const [value, setValue] = useState(initial);
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);

  useEffect(() => {
    inputRef.current?.focus();
    inputRef.current?.select();
  }, []);

  const handleKey = (
    event: ReactKeyboardEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onCommit(value);
    } else if (event.key === "Escape") {
      event.preventDefault();
      onCancel();
    }
  };

  if (multiline) {
    return (
      <textarea
        ref={inputRef as React.RefObject<HTMLTextAreaElement>}
        className={styles.cellEditor}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => onCommit(value)}
        onKeyDown={handleKey}
        placeholder={placeholder}
        rows={2}
      />
    );
  }
  return (
    <input
      ref={inputRef as React.RefObject<HTMLInputElement>}
      type="text"
      className={styles.cellEditor}
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onBlur={() => onCommit(value)}
      onKeyDown={handleKey}
      placeholder={placeholder}
    />
  );
}

export const ruleTableStyles = styles;
