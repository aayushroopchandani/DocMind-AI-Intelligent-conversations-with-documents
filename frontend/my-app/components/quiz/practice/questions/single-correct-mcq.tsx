import type { OptionKey, SingleCorrectMCQQuestion } from "@/lib/quiz/types";
import { OptionRow, type OptionState } from "@/components/quiz/shared/option-row";

interface Props {
  question: SingleCorrectMCQQuestion;
  selected: OptionKey | null;
  submitted: boolean;
  onSelect: (option: OptionKey) => void;
}

const KEYS: OptionKey[] = ["A", "B", "C", "D"];

export function SingleCorrectMCQ({
  question,
  selected,
  submitted,
  onSelect,
}: Props) {
  const correctKey = question.correct_answer.option;

  function stateFor(key: OptionKey): OptionState {
    if (!submitted) return key === selected ? "selected" : "idle";
    if (key === correctKey) return "correct";
    if (key === selected) return "incorrect";
    return "idle";
  }

  return (
    <div className="space-y-2.5">
      {KEYS.map((key, i) => (
        <OptionRow
          key={key}
          index={i}
          optionKey={key}
          text={question.options[key]}
          state={stateFor(key)}
          disabled={submitted}
          onClick={() => onSelect(key)}
        />
      ))}
    </div>
  );
}
