import { Panel } from '@/components/Panel';
import { useMessages } from '@/locales';

interface PlaceholderPageProps {
  title: string;
  subtitle?: string;
}

/**
 * Generic empty page used while sub-page content is still being designed.
 * Renders just the section header — no inspector, no run controls — matching
 * the simplified shell used outside the Run pages.
 */
export function PlaceholderPage({ title, subtitle }: PlaceholderPageProps) {
  const messages = useMessages();
  return (
    <Panel title={title} subtitle={subtitle ?? messages.common.placeholder}>
      <div style={{ height: 12 }} />
    </Panel>
  );
}
