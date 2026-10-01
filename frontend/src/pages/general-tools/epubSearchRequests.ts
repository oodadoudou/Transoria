// Search requests share the editor's mutation lock; only the latest may publish.
export class SearchRequests {
  private revision = 0;
  private pending: number | null = null;

  invalidate(): number {
    this.pending = null;
    return ++this.revision;
  }

  enqueue(revision: number): boolean {
    if (!this.isCurrent(revision)) return false;
    this.pending = revision;
    return true;
  }

  take(): number | null {
    const revision = this.pending;
    this.pending = null;
    return revision;
  }

  isCurrent(revision: number): boolean {
    return revision === this.revision;
  }
}
