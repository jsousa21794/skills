import { Document, Packer, Paragraph, TextRun, InsertedTextRun, DeletedTextRun } from "docx";
import { diffWords } from "diff";
import { splitParagraphs } from "../text";

/**
 * Exporta o texto revisto como .docx com alterações registadas (tracked changes),
 * comparando parágrafo a parágrafo com o original. Quando o número de parágrafos
 * difere, a comparação é feita sobre o texto inteiro num único parágrafo por bloco.
 */
export async function buildTrackedChangesDocx(original: string, revised: string, author = "Prosa"): Promise<Blob> {
  const originals = splitParagraphs(original);
  const revisedParagraphs = splitParagraphs(revised);
  const pairs: [string, string][] =
    originals.length === revisedParagraphs.length ? originals.map((o, i) => [o, revisedParagraphs[i]]) : [[original.replace(/\n\s*\n/g, "\n"), revised.replace(/\n\s*\n/g, "\n")]];

  const date = new Date().toISOString();
  let id = 1;
  const paragraphs = pairs.map(([before, after]) => {
    const runs = diffWords(before, after).map((part) => {
      if (part.added) return new InsertedTextRun({ text: part.value, id: id++, author, date });
      if (part.removed) return new DeletedTextRun({ text: part.value, id: id++, author, date });
      return new TextRun(part.value);
    });
    return new Paragraph({ children: runs });
  });

  const document = new Document({
    creator: author,
    title: "Revisão Prosa",
    features: { trackRevisions: true },
    sections: [{ children: paragraphs }],
  });
  return Packer.toBlob(document);
}
