"use client";

import { useEffect, useState } from "react";
import { Select } from "@/components/ui/select";
import { getAssistantModel, setAssistantModel } from "@/lib/assistant-model-api";

interface ModelOption {
  id: string;
  name: string;
}

export function AssistantModelPicker() {
  const [current, setCurrent] = useState<string>("");
  const [available, setAvailable] = useState<ModelOption[]>([]);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getAssistantModel()
      .then((state) => {
        if (cancelled) return;
        setAvailable(state.available);
        setCurrent(state.current);
        setLoaded(true);
      })
      .catch(() => {
        // Fail silently — don't crash the assistant panel
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!loaded || available.length === 0) return null;

  async function handleChange(next: string) {
    const prev = current;
    setCurrent(next); // optimistic
    try {
      await setAssistantModel(next);
    } catch {
      setCurrent(prev); // revert on failure
    }
  }

  return (
    <Select
      value={current}
      onChange={(next) => { void handleChange(next); }}
      ariaLabel="Assistant model"
      fitLongestOption
      selectClassName="h-auto rounded border-border px-1.5 py-0.5 pr-7 text-[10px] text-muted-foreground"
    >
      {available.map((m) => (
        <option key={m.id} value={m.id}>
          {m.name}
        </option>
      ))}
    </Select>
  );
}
