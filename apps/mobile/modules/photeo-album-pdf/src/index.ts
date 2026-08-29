export type AlbumPdfResult = {
  pageCount: number;
  pageHeight: number;
  pageWidth: number;
  uri: string;
};

export type RenderedAlbumPage = {
  height: number;
  uri: string;
  width: number;
};

type AlbumPdfNative = {
  generate(albumKey: string, documentJson: string): Promise<AlbumPdfResult>;
  pageCount(uri: string): Promise<number>;
  renderPage(uri: string, pageIndex: number, width: number): Promise<RenderedAlbumPage>;
};

let cached: AlbumPdfNative | null | undefined;

async function nativeModule(): Promise<AlbumPdfNative> {
  if (cached) return cached;
  if (cached === null) throw new Error("The in-app album reader is unavailable on this build.");
  try {
    const { requireOptionalNativeModule } = await import("expo");
    cached = requireOptionalNativeModule<AlbumPdfNative>("PhoteoAlbumPdf") ?? null;
  } catch {
    cached = null;
  }
  if (!cached) throw new Error("The in-app album reader is unavailable on this build.");
  return cached;
}

export async function generateAlbumPdf(
  albumKey: string,
  document: unknown,
): Promise<AlbumPdfResult> {
  return (await nativeModule()).generate(albumKey, JSON.stringify(document));
}

export async function albumPdfPageCount(uri: string): Promise<number> {
  return (await nativeModule()).pageCount(uri);
}

export async function renderAlbumPdfPage(
  uri: string,
  pageIndex: number,
  width: number,
): Promise<RenderedAlbumPage> {
  return (await nativeModule()).renderPage(uri, pageIndex, width);
}
