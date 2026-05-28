import type { GeneralToolsPage } from '@/store/useTaskStore';
import { BatchReplacementPage } from './BatchReplacementPage';
import { EpubCompressPage } from './EpubCompressPage';
import { EpubConvertPage } from './EpubConvertPage';
import { EpubMetadataPage } from './EpubMetadataPage';
import { EpubMergePage } from './EpubMergePage';

interface GeneralToolsModuleProps {
  page: GeneralToolsPage;
}

export function GeneralToolsModule({ page }: GeneralToolsModuleProps) {
  switch (page) {
    case 'batchReplacement':
      return <BatchReplacementPage />;
    case 'epubCompress':
      return <EpubCompressPage />;
    case 'epubMerge':
      return <EpubMergePage />;
    case 'epubConvert':
      return <EpubConvertPage />;
    case 'epubMetadata':
      return <EpubMetadataPage />;
  }
}
