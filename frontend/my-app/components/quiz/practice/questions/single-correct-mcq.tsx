import type { OptionKey, SingleCorrectMCQQuestion } from "@/lib/quiz/types";
import { OptionRow, type OptionState } from "@/components/quiz/shared/option-row";

interface Props {
  question: SingleCorrectMCQQuestion;
  selected: OptionKey | null;
  onSelect: (option: OptionKey) => void;
}

const KEYS: OptionKey[] = ["A", "B", "C", "D"];

export function SingleCorrectMCQ({
  question,
  selected,
  onSelect,
}: Props) {
  function stateFor(key: OptionKey): OptionState {
    return key === selected ? "selected" : "idle";
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
          disabled={false}
          onClick={() => onSelect(key)}
        />
      ))}
    </div>
  );
}
