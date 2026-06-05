"use client";

import { useEffect, useState } from "react";
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

  async function handleChange(e: React.ChangeEvent<HTMLSelectElement>) {
    const next = e.target.value;
    const prev = current;
    setCurrent(next); // optimistic
    try {
      await setAssistantModel(next);
    } catch {
      setCurrent(prev); // revert on failure
    }
  }

  return (
    <select
      value={current}
      onChange={handleChange}
      aria-label="Assistant model"
      className="rounded border border-border bg-background px-1.5 py-0.5 text-[10px] text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
    >
      {available.map((m) => (
        <option key={m.id} value={m.id}>
          {m.name}
        </option>
      ))}
    </select>
  );
}
