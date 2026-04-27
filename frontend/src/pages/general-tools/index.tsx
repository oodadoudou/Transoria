import type { GeneralToolsPage } from '@/store/useTaskStore';
import { BatchReplacementPage } from './BatchReplacementPage';

interface GeneralToolsModuleProps {
  page: GeneralToolsPage;
}

export function GeneralToolsModule({ page }: GeneralToolsModuleProps) {
  switch (page) {
    case 'batchReplacement':
      return <BatchReplacementPage />;
  }
}
