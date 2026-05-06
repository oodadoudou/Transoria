import type { GeneralToolsPage } from '@/store/useTaskStore';
import { BatchReplacementPage } from './BatchReplacementPage';
import { EpubOrganizePage } from './EpubOrganizePage';
import { EpubCompressPage } from './EpubCompressPage';
import { EpubMergePage } from './EpubMergePage';

interface GeneralToolsModuleProps {
  page: GeneralToolsPage;
}

export function GeneralToolsModule({ page }: GeneralToolsModuleProps) {
  switch (page) {
    case 'batchReplacement':
      return <BatchReplacementPage />;
    case 'epubOrganize':
      return <EpubOrganizePage />;
    case 'epubCompress':
      return <EpubCompressPage />;
    case 'epubMerge':
      return <EpubMergePage />;
  }
}
