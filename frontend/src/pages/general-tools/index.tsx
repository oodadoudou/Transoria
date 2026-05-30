import type { GeneralToolsPage } from '@/store/useTaskStore';
import { BatchReplacementPage } from './BatchReplacementPage';
import { EpubToolsPage } from './EpubToolsPage';

interface GeneralToolsModuleProps {
  page: GeneralToolsPage;
}

export function GeneralToolsModule({ page }: GeneralToolsModuleProps) {
  switch (page) {
    case 'batchReplacement':
      return <BatchReplacementPage />;
    case 'epubTools':
      return <EpubToolsPage />;
    case 'epubCompress':
      return <EpubToolsPage initialTool="epubCompress" />;
    case 'epubMerge':
      return <EpubToolsPage initialTool="epubMerge" />;
    case 'epubConvert':
      return <EpubToolsPage initialTool="epubConvert" />;
    case 'epubMetadata':
      return <EpubToolsPage initialTool="epubMetadata" />;
    case 'epubRepair':
      return <EpubToolsPage initialTool="epubRepair" />;
    case 'txtToEpub':
      return <EpubToolsPage initialTool="txtToEpub" />;
  }
}
