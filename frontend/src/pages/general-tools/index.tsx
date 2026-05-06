import type { GeneralToolsPage } from '@/store/useTaskStore';
import { useMessages } from '@/locales';
import { PlaceholderPage } from '../PlaceholderPage';
import { BatchReplacementPage } from './BatchReplacementPage';
import { EpubOrganizePage } from './EpubOrganizePage';

interface GeneralToolsModuleProps {
  page: GeneralToolsPage;
}

export function GeneralToolsModule({ page }: GeneralToolsModuleProps) {
  const messages = useMessages();
  const tools = messages.generalTools;
  switch (page) {
    case 'batchReplacement':
      return <BatchReplacementPage />;
    case 'epubOrganize':
      return <EpubOrganizePage />;
    case 'epubCompress':
      return (
        <PlaceholderPage
          title={tools.epubCompress.title}
          subtitle={tools.epubCompress.sub}
        />
      );
    case 'epubMerge':
      return (
        <PlaceholderPage
          title={tools.epubMerge.title}
          subtitle={tools.epubMerge.sub}
        />
      );
  }
}
