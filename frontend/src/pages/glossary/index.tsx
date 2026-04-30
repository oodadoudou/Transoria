import { useMessages } from '@/locales';
import type { GlossaryPage } from '@/store/useTaskStore';
import { PlaceholderPage } from '../PlaceholderPage';
import { PromptConfigPage } from '../shared/PromptConfigPage';
import { RunPage } from './RunPage';
import { SettingsPage } from './SettingsPage';

interface GlossaryModuleProps {
  page: GlossaryPage;
}

export function GlossaryModule({ page }: GlossaryModuleProps) {
  const messages = useMessages();
  if (page === 'run') return <RunPage />;
  if (page === 'settings') return <SettingsPage />;
  if (page === 'prompt') return <PromptConfigPage owner="glossary" />;
  return <PlaceholderPage title={messages.pages.glossary[page]} />;
}
