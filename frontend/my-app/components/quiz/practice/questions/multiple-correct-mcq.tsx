import type { MultipleCorrectMCQQuestion, OptionKey } from "@/lib/quiz/types";
import { OptionRow, type OptionState } from "@/components/quiz/shared/option-row";

interface Props {
  question: MultipleCorrectMCQQuestion;
  selected: OptionKey[];
  submitted: boolean;
  onToggle: (option: OptionKey) => void;
}

const KEYS: OptionKey[] = ["A", "B", "C", "D"];

export function MultipleCorrectMCQ({
  question,
  selected,
  submitted,
  onToggle,
}: Props) {
  const correctSet = new Set(question.correct_answers.map((a) => a.option));

  function stateFor(key: OptionKey): OptionState {
    const isSelected = selected.includes(key);
    if (!submitted) return isSelected ? "selected" : "idle";
    const isCorrect = correctSet.has(key);
    if (isCorrect && isSelected) return "correct";
    if (isCorrect && !isSelected) return "missed";
    if (!isCorrect && isSelected) return "incorrect";
    return "idle";
  }

  return (
    <div className="space-y-2.5">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        Select all that apply
      </p>
      {KEYS.map((key, i) => (
        <OptionRow
          key={key}
          index={i}
          multi
          optionKey={key}
          text={question.options[key]}
          state={stateFor(key)}
          disabled={submitted}
          onClick={() => onToggle(key)}
        />
      ))}
    </div>
  );
}
