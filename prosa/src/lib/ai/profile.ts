import { buildProfileSystemPrompt, buildProfileUserPrompt } from "../prompt";
import { profileOutputSchema } from "../schemas";
import type { Language, VoiceProfile } from "../types";
import { computeVoiceMetrics } from "../voice";
import type { Generate } from "./client";

export async function buildVoiceProfile(examples: string[], language: Language, generate: Generate, signal?: AbortSignal): Promise<VoiceProfile> {
  const metrics = computeVoiceMetrics(examples, language);
  const output = await generate({
    system: buildProfileSystemPrompt(language),
    user: buildProfileUserPrompt(examples),
    schema: profileOutputSchema,
    maxTokens: 4000,
    signal,
  });
  return {
    name: "O meu perfil",
    vocabulary: output.vocabulary.trim(),
    rhythm: output.rhythm.trim(),
    formality: output.formality.trim(),
    sentenceStructure: output.sentenceStructure.trim(),
    avoid: output.avoid.trim(),
    metrics,
    avoidWords: [],
    preferredTerms: [],
  };
}
