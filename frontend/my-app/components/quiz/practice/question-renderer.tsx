import type { QuizAnswer, QuizQuestion } from "@/lib/quiz/types";
import { SingleCorrectMCQ } from "./questions/single-correct-mcq";
import { MultipleCorrectMCQ } from "./questions/multiple-correct-mcq";
import { TrueFalse } from "./questions/true-false";
import { FillInTheBlank } from "./questions/fill-in-the-blank";
import { MatchTheFollowing } from "./questions/match-the-following";

interface QuestionRendererProps {
  question: QuizQuestion;
  answer: QuizAnswer;
  onChange: (answer: QuizAnswer) => void;
}

/**
 * Renders the prompt + the format-specific answer control for one question.
 * Keeps all per-format narrowing in one place so the orchestrator stays generic.
 */
export function QuestionRenderer({
  question,
  answer,
  onChange,
}: QuestionRendererProps) {
  return (
    <div className="space-y-5">
      {/*
       * Fill-in-the-blank renders its prompt inline (with the input fields), so
       * showing a separate heading here would duplicate the sentence.
       */}
      {question.type !== "fill_in_the_blank" ? (
        <h2 className="text-lg font-semibold leading-snug text-foreground">
          {question.type === "true_false"
            ? question.statement
            : question.question}
        </h2>
      ) : null}

      {question.type === "single_correct_mcq" &&
        answer.type === "single_correct_mcq" && (
          <SingleCorrectMCQ
            question={question}
            selected={answer.option}
            onSelect={(option) =>
              onChange({ type: "single_correct_mcq", option })
            }
          />
        )}

      {question.type === "multiple_correct_mcq" &&
        answer.type === "multiple_correct_mcq" && (
          <MultipleCorrectMCQ
            question={question}
            selected={answer.options}
            onToggle={(option) => {
              const set = new Set(answer.options);
              if (set.has(option)) set.delete(option);
              else set.add(option);
              onChange({
                type: "multiple_correct_mcq",
                options: [...set],
              });
            }}
          />
        )}

      {question.type === "true_false" && answer.type === "true_false" && (
        <TrueFalse
          question={question}
          value={answer.value}
          onSelect={(value) => onChange({ type: "true_false", value })}
        />
      )}

      {question.type === "fill_in_the_blank" &&
        answer.type === "fill_in_the_blank" && (
          <FillInTheBlank
            question={question}
            values={answer.values}
            onChange={(blankId, value) =>
              onChange({
                type: "fill_in_the_blank",
                values: { ...answer.values, [blankId]: value },
              })
            }
          />
        )}

      {question.type === "match_the_following" &&
        answer.type === "match_the_following" && (
          <MatchTheFollowing
            question={question}
            pairs={answer.pairs}
            onChange={(leftId, rightId) =>
              onChange({
                type: "match_the_following",
                pairs: { ...answer.pairs, [leftId]: rightId },
              })
            }
          />
        )}
    </div>
  );
}
