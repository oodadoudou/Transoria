import { useMessages } from '@/locales';
import { dialogsBridge, BridgeError } from '@/bridge';
import { FieldCard } from '@/components/FieldCard';
import styles from './FolderPickerRow.module.css';

interface FolderPickerRowProps {
  label: string;
  value: string;
  variant: 'input' | 'output';
  onChange: (path: string) => void;
  onError?: (error: BridgeError) => void;
}

export function FolderPickerRow({
  label,
  value,
  variant,
  onChange,
  onError,
}: FolderPickerRowProps) {
  const messages = useMessages();
  const buttonLabel = messages.folderPicker.choose;

  const handlePick = async () => {
    try {
      const result =
        variant === 'input'
          ? await dialogsBridge.chooseInputDirectory(value || undefined)
          : await dialogsBridge.chooseOutputDirectory(value || undefined);
      if (result.path) onChange(result.path);
    } catch (error) {
      if (BridgeError.isBridgeError(error) && onError) {
        onError(error);
      }
    }
  };

  return (
    <div className={styles.row}>
      <div className={styles.field}>
        <FieldCard label={label} value={value || ''} trailing="folder" truncate />
      </div>
      <button type="button" className={styles.pickBtn} onClick={handlePick}>
        {buttonLabel}
      </button>
    </div>
  );
}
