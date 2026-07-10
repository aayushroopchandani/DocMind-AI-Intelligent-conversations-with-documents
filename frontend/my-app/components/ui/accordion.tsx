"use client";

import { createContext, useContext } from "react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

type AccordionContextValue = {
  value: string[];
  onValueChange: (value: string[]) => void;
};

const AccordionContext = createContext<AccordionContextValue | null>(null);

function useAccordion() {
  const context = useContext(AccordionContext);
  if (!context) {
    throw new Error("Accordion components must be used inside Accordion");
  }
  return context;
}

function Accordion({
  value,
  onValueChange,
  children,
  className,
}: {
  value: string[];
  onValueChange: (value: string[]) => void;
  children: ReactNode;
  className?: string;
}) {
  return (
    <AccordionContext.Provider value={{ value, onValueChange }}>
      <div className={cn("space-y-3", className)}>{children}</div>
    </AccordionContext.Provider>
  );
}

function AccordionItem({
  value,
  children,
  className,
}: {
  value: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div data-accordion-value={value} className={cn("min-w-0", className)}>
      {children}
    </div>
  );
}

function AccordionTrigger({
  itemValue,
  className,
  children,
  onClick,
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { itemValue: string }) {
  const { value, onValueChange } = useAccordion();
  const open = value.includes(itemValue);

  return (
    <button
      type="button"
      aria-expanded={open}
      className={cn(className)}
      onClick={(event) => {
        onClick?.(event);
        if (event.defaultPrevented) return;

        onValueChange(
          open
            ? value.filter((entry) => entry !== itemValue)
            : [...value, itemValue],
        );
      }}
      {...props}
    >
      {children}
    </button>
  );
}

function AccordionContent({
  itemValue,
  className,
  children,
  ...props
}: React.ComponentProps<"div"> & { itemValue: string }) {
  const { value } = useAccordion();
  const open = value.includes(itemValue);

  return (
    <div
      hidden={!open}
      data-state={open ? "open" : "closed"}
      className={cn(
        "overflow-hidden data-[state=open]:animate-in data-[state=open]:fade-in-0",
        className,
      )}
      {...props}
    >
      {children}
    </div>
  );
}

export { Accordion, AccordionContent, AccordionItem, AccordionTrigger };
