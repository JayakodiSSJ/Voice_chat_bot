// Strips Markdown so the speech engine reads plain words.
export const cleanForSpeech = (text) =>
  String(text || "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^\s*[-*+]\s+/gm, "")
    .replace(/^\s*\d+[.)]\s+/gm, "")
    .replace(/[*_`~]/g, "")
    .replace(/\s+/g, " ")
    .trim();

// Sentence-ending punctuation followed by whitespace. Requiring the whitespace keeps
// decimals such as "1.5 GB" together. Includes the ellipsis and the Sinhala kunddaliya.
const SENTENCE_END = /([.!?…෴])\s+/gu;
const BREAK = "\u0000";
// A period after these words (or a single letter) does not end a sentence: "Rs. 1500".
const ABBREVIATION_END = /(?:^|\s)(?:rs|mr|mrs|ms|dr|no|st|vs|etc|e\.g|i\.e|approx|[a-z])\.$/i;

const splitSentences = (line) =>
  line
    .replace(SENTENCE_END, `$1${BREAK}`)
    .split(BREAK)
    .reduce((sentences, piece) => {
      const previous = sentences[sentences.length - 1];
      if (previous !== undefined && ABBREVIATION_END.test(previous)) {
        sentences[sentences.length - 1] = `${previous} ${piece}`;
      } else {
        sentences.push(piece);
      }
      return sentences;
    }, []);

/**
 * Splits a reply into speakable chunks so the first one can be synthesized and played
 * while the rest are still being generated. Each line (e.g. a list item) and each
 * sentence becomes a chunk; fragments shorter than `minChars` (such as "Rs.") are merged
 * into their neighbour as long as the result stays within `maxChars`.
 */
export const splitIntoSpeechChunks = (text, { minChars = 15, maxChars = 280 } = {}) => {
  const sentences = String(text || "")
    .split(/\n+/)
    .map(cleanForSpeech)
    .filter(Boolean)
    .flatMap(splitSentences)
    .map((sentence) => sentence.trim())
    .filter(Boolean);

  const chunks = [];
  sentences.forEach((sentence) => {
    const last = chunks[chunks.length - 1];
    const canMerge =
      last !== undefined &&
      (last.length < minChars || sentence.length < minChars) &&
      last.length + 1 + sentence.length <= maxChars;
    if (canMerge) {
      chunks[chunks.length - 1] = `${last} ${sentence}`;
    } else {
      chunks.push(sentence);
    }
  });
  return chunks;
};
