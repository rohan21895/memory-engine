import type { AlbumSpec } from "../../../contracts/codegen/generated/typescript/index.js";

import type { CheckedIccProfile } from "./icc.js";
import type { RenderedPage } from "./page.js";

const ASCII = (value: string): Buffer => Buffer.from(value, "ascii");
const MM_TO_POINTS = 72 / 25.4;

function xmlEscape(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&apos;");
}

function pdfHexString(value: string): string {
  const utf16 = Buffer.alloc(2 + value.length * 2);
  utf16.writeUInt16BE(0xfeff, 0);
  for (let index = 0; index < value.length; index += 1) {
    utf16.writeUInt16BE(value.charCodeAt(index), 2 + index * 2);
  }
  return `<${utf16.toString("hex").toUpperCase()}>`;
}

function decimal(value: number): string {
  return value.toFixed(6).replace(/\.?(?:0+)$/, "");
}

function stream(dictionary: string, contents: Buffer): Buffer {
  return Buffer.concat([
    ASCII(`<< ${dictionary} /Length ${contents.length} >>\nstream\n`),
    contents,
    ASCII("\nendstream"),
  ]);
}

function xmp(spec: AlbumSpec, icc: CheckedIccProfile): Buffer {
  const title = xmlEscape(spec.title ?? "");
  return Buffer.from(
    `<?xpacket begin="\uFEFF" id="W5M0MpCehiHzreSzNTczkc9d"?>\n` +
      `<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="Memory Engine render-print/1">\n` +
      `<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n` +
      `<rdf:Description rdf:about="" xmlns:pdfxid="http://www.npes.org/pdfx/ns/id/" pdfxid:GTS_PDFXVersion="PDF/X-4"/>\n` +
      `<rdf:Description rdf:about="" xmlns:xmp="http://ns.adobe.com/xap/1.0/" ` +
      `xmp:CreateDate="2000-01-01T00:00:00Z" xmp:ModifyDate="2000-01-01T00:00:00Z" xmp:MetadataDate="2000-01-01T00:00:00Z"/>\n` +
      `<rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">` +
      `<dc:format>application/pdf</dc:format><dc:title><rdf:Alt><rdf:li xml:lang="x-default">${title}</rdf:li></rdf:Alt></dc:title>` +
      `</rdf:Description>\n` +
      `<rdf:Description rdf:about="" xmlns:me="https://memory-engine.local/pdfx/1.0/" ` +
      `me:albumId="${spec.album_id}" me:iccBlake3="${icc.digest}"/>\n` +
      `</rdf:RDF></x:xmpmeta>\n<?xpacket end="w"?>`,
    "utf8",
  );
}

function renderingIntent(intent: AlbumSpec["vendor_profile"]["color_profile"]["intent"]): string {
  return {
    perceptual: "Perceptual",
    relative_colorimetric: "RelativeColorimetric",
    saturation: "Saturation",
    absolute_colorimetric: "AbsoluteColorimetric",
  }[intent];
}

export function buildDeterministicPdf(
  spec: AlbumSpec,
  pages: readonly RenderedPage[],
  icc: CheckedIccProfile,
): Buffer {
  if (pages.length !== spec.pages.length) throw new Error("Rendered page count does not match AlbumSpec.");

  const objects: Buffer[] = [];
  const add = (value: string | Buffer): number => {
    objects.push(typeof value === "string" ? ASCII(value) : value);
    return objects.length;
  };

  const catalogRef = add("");
  const pagesRef = add("");
  const outputIntentRef = add("");
  const metadataRef = add(stream("/Type /Metadata /Subtype /XML", xmp(spec, icc)));
  const infoRef = add(
    `<< /Title ${pdfHexString(spec.title ?? "")} /Producer ${pdfHexString("Memory Engine render-print/1")} ` +
      `/CreationDate (D:20000101000000Z) /ModDate (D:20000101000000Z) /GTS_PDFXVersion (PDF/X-4) /Trapped /False >>`,
  );
  const iccRef = add(stream(`/N ${icc.components} /Alternate /Device${icc.colorSpace === "cmyk" ? "CMYK" : "RGB"}`, icc.bytes));

  const widthPoints = (spec.vendor_profile.trim_size_mm.width_mm + spec.vendor_profile.bleed_mm * 2) * MM_TO_POINTS;
  const heightPoints = (spec.vendor_profile.trim_size_mm.height_mm + spec.vendor_profile.bleed_mm * 2) * MM_TO_POINTS;
  const bleedPoints = spec.vendor_profile.bleed_mm * MM_TO_POINTS;
  const pageRefs: number[] = [];
  const intent = renderingIntent(spec.vendor_profile.color_profile.intent);

  for (const page of pages) {
    const decode = icc.components === 4 ? " /Decode [1 0 1 0 1 0 1 0]" : "";
    const imageRef = add(
      stream(
        `/Type /XObject /Subtype /Image /Width ${page.widthPx} /Height ${page.heightPx} ` +
          `/ColorSpace [/ICCBased ${iccRef} 0 R] /BitsPerComponent 8 /Filter /DCTDecode /Interpolate false${decode}`,
        page.jpeg,
      ),
    );
    const content = ASCII(
      `q\n/${intent} ri\n${decimal(widthPoints)} 0 0 ${decimal(heightPoints)} 0 0 cm\n/Im0 Do\nQ\n`,
    );
    const contentRef = add(stream("", content));
    pageRefs.push(
      add(
        `<< /Type /Page /Parent ${pagesRef} 0 R ` +
          `/MediaBox [0 0 ${decimal(widthPoints)} ${decimal(heightPoints)}] ` +
          `/BleedBox [0 0 ${decimal(widthPoints)} ${decimal(heightPoints)}] ` +
          `/TrimBox [${decimal(bleedPoints)} ${decimal(bleedPoints)} ${decimal(widthPoints - bleedPoints)} ${decimal(heightPoints - bleedPoints)}] ` +
          `/Resources << /XObject << /Im0 ${imageRef} 0 R >> >> /Contents ${contentRef} 0 R >>`,
      ),
    );
  }

  objects[catalogRef - 1] = ASCII(
    `<< /Type /Catalog /Version /1.6 /Pages ${pagesRef} 0 R /Metadata ${metadataRef} 0 R ` +
      `/OutputIntents [${outputIntentRef} 0 R] /ViewerPreferences << /DisplayDocTitle true >> >>`,
  );
  objects[pagesRef - 1] = ASCII(
    `<< /Type /Pages /Count ${pageRefs.length} /Kids [${pageRefs.map((ref) => `${ref} 0 R`).join(" ")}] >>`,
  );
  objects[outputIntentRef - 1] = ASCII(
    `<< /Type /OutputIntent /S /GTS_PDFX /OutputConditionIdentifier ${pdfHexString(icc.name)} ` +
      `/Info ${pdfHexString(icc.name)} /RegistryName (http://www.color.org) /DestOutputProfile ${iccRef} 0 R >>`,
  );

  const chunks: Buffer[] = [Buffer.from("%PDF-1.6\n%\xE2\xE3\xCF\xD3\n", "binary")];
  const offsets = [0];
  let byteOffset = chunks[0]?.length ?? 0;
  objects.forEach((object, index) => {
    offsets.push(byteOffset);
    const wrapped = Buffer.concat([ASCII(`${index + 1} 0 obj\n`), object, ASCII("\nendobj\n")]);
    chunks.push(wrapped);
    byteOffset += wrapped.length;
  });

  const xrefOffset = byteOffset;
  const xref = ["xref", `0 ${objects.length + 1}`, "0000000000 65535 f "];
  for (let index = 1; index <= objects.length; index += 1) {
    xref.push(`${String(offsets[index]).padStart(10, "0")} 00000 n `);
  }
  const documentId = spec.album_id.slice(0, 32).padEnd(32, "0");
  xref.push(
    "trailer",
    `<< /Size ${objects.length + 1} /Root ${catalogRef} 0 R /Info ${infoRef} 0 R /ID [<${documentId}><${documentId}>] >>`,
    "startxref",
    String(xrefOffset),
    "%%EOF",
    "",
  );
  chunks.push(ASCII(xref.join("\n")));
  return Buffer.concat(chunks);
}
