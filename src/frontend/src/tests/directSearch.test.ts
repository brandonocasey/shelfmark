import { describe, expect, it } from 'vitest';

import { buildDirectSearchUrl } from '../services/api';

describe('buildDirectSearchUrl', () => {
  it('routes ebook searches to direct download', () => {
    const url = new URL(buildDirectSearchUrl('query=dune&format=epub', 'ebook'), 'http://test');

    expect(url.pathname).toBe('/api/releases');
    expect(url.searchParams.get('source')).toBe('direct_download');
    expect(url.searchParams.get('content_type')).toBe('ebook');
    expect(url.searchParams.get('query')).toBe('dune');
    expect(url.searchParams.get('format')).toBe('epub');
  });

  it('routes audiobook searches to AudiobookBay', () => {
    const url = new URL(buildDirectSearchUrl('query=dune', 'audiobook'), 'http://test');

    expect(url.searchParams.get('source')).toBe('audiobookbay');
    expect(url.searchParams.get('content_type')).toBe('audiobook');
  });
});
