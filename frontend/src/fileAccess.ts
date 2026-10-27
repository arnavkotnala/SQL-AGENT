/**
 * Browser File System Access API (Chrome / Edge desktop). Lets a *deployed* LoopSQL write approved
 * changes straight back into the Excel file the user opened from their own computer.
 * Other browsers fall back to downloading the edited copy.
 */

interface PermissionCapableHandle extends FileSystemFileHandle {
  queryPermission?: (d: { mode: "read" | "readwrite" }) => Promise<PermissionState>;
  requestPermission?: (d: { mode: "read" | "readwrite" }) => Promise<PermissionState>;
}

type PickerWindow = Window & {
  showOpenFilePicker?: (opts: unknown) => Promise<FileSystemFileHandle[]>;
};

export const canWriteBack = (): boolean => typeof (window as PickerWindow).showOpenFilePicker === "function";

export async function pickSpreadsheet(): Promise<FileSystemFileHandle | null> {
  const w = window as PickerWindow;
  if (!w.showOpenFilePicker) return null;
  try {
    const [handle] = await w.showOpenFilePicker({
      multiple: false,
      types: [
        {
          description: "Spreadsheets and databases",
          accept: {
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
            "application/vnd.ms-excel.sheet.macroEnabled.12": [".xlsm"],
            "text/csv": [".csv"],
            "application/x-sqlite3": [".db", ".sqlite", ".sqlite3"],
          },
        },
      ],
    });
    return handle;
  } catch {
    return null; // user cancelled
  }
}

/** Must be called from a click handler (browsers require a user gesture). */
export async function ensureWritePermission(handle: FileSystemFileHandle): Promise<boolean> {
  const h = handle as PermissionCapableHandle;
  if (!h.requestPermission) return true;
  if ((await h.queryPermission?.({ mode: "readwrite" })) === "granted") return true;
  return (await h.requestPermission({ mode: "readwrite" })) === "granted";
}

/** Overwrite the user's file. Returns the previous bytes so the change can be undone. */
export async function writeBack(handle: FileSystemFileHandle, data: Blob): Promise<ArrayBuffer> {
  const h = handle as PermissionCapableHandle;
  const previous = await (await h.getFile()).arrayBuffer();
  const writable = await h.createWritable();
  await writable.write(data);
  await writable.close();
  return previous;
}

export async function restore(handle: FileSystemFileHandle, previous: ArrayBuffer): Promise<void> {
  const writable = await (handle as PermissionCapableHandle).createWritable();
  await writable.write(previous);
  await writable.close();
}
