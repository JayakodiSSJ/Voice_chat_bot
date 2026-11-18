import { cleanForSpeech, splitIntoSpeechChunks } from "./speechChunks";

describe("speech chunking", () => {
  test("splits English sentences into separate chunks", () => {
    expect(
      splitIntoSpeechChunks(
        "Our fiber package gives you fast internet. It costs less than you think! Would you like to order it?"
      )
    ).toEqual([
      "Our fiber package gives you fast internet.",
      "It costs less than you think!",
      "Would you like to order it?",
    ]);
  });

  test("splits Sinhala sentences", () => {
    expect(
      splitIntoSpeechChunks("ඔබට මාසෙකට හොඳ ඩේටා පැකේජ් එකක් තියෙනවා. ඒක ගන්න කැමතිද කියලා කියන්න?")
    ).toEqual(["ඔබට මාසෙකට හොඳ ඩේටා පැකේජ් එකක් තියෙනවා.", "ඒක ගන්න කැමතිද කියලා කියන්න?"]);
  });

  test("keeps decimals together and merges short fragments", () => {
    expect(splitIntoSpeechChunks("The price is Rs. 1500 for 1.5 GB of data per day on this plan.")).toEqual([
      "The price is Rs. 1500 for 1.5 GB of data per day on this plan.",
    ]);
    expect(splitIntoSpeechChunks("Please contact Dr. Perera today. Our office opens at nine.")).toEqual([
      "Please contact Dr. Perera today.",
      "Our office opens at nine.",
    ]);
    expect(splitIntoSpeechChunks("Sure. Here is the package that fits your monthly usage best.")).toEqual([
      "Sure. Here is the package that fits your monthly usage best.",
    ]);
  });

  test("treats list items as separate chunks and strips Markdown", () => {
    expect(
      splitIntoSpeechChunks(
        "Here are the **available** packages for you:\n- Fiber 100 Mbps unlimited home plan\n- 4G Wi-Fi router plan with 40 GB"
      )
    ).toEqual([
      "Here are the available packages for you:",
      "Fiber 100 Mbps unlimited home plan",
      "4G Wi-Fi router plan with 40 GB",
    ]);
  });

  test("does not merge beyond the maximum chunk length", () => {
    const long = "a".repeat(278);
    expect(splitIntoSpeechChunks(`${long}. Ok.`)).toEqual([`${long}.`, "Ok."]);
  });

  test("returns no chunks for empty or Markdown-only text", () => {
    expect(splitIntoSpeechChunks("")).toEqual([]);
    expect(splitIntoSpeechChunks("  \n ** \n")).toEqual([]);
    expect(cleanForSpeech("[link](https://example.com) `code`")).toBe("link code");
  });
});
