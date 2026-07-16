import type { MultipleCorrectMCQQuestion, OptionKey } from "@/lib/quiz/types";
import { OptionRow, type OptionState } from "@/components/quiz/shared/option-row";

interface Props {
  question: MultipleCorrectMCQQuestion;
  selected: OptionKey[];
  onToggle: (option: OptionKey) => void;
}

const KEYS: OptionKey[] = ["A", "B", "C", "D"];

export function MultipleCorrectMCQ({
  question,
  selected,
  onToggle,
}: Props) {
  function stateFor(key: OptionKey): OptionState {
    return selected.includes(key) ? "selected" : "idle";
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
          disabled={false}
          onClick={() => onToggle(key)}
        />
      ))}
    </div>
  );
}
