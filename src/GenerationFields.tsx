import { Form, InputNumber, Switch } from "antd";
import type { GenerationConfig } from "./types";

const fields: {
  key: keyof GenerationConfig;
  label: string;
  min: number;
  max: number;
  step: number;
  initial: number;
}[] = [
  {
    key: "temperature",
    label: "Temperature",
    min: 0,
    max: 2,
    step: 0.1,
    initial: 0.2,
  },
  { key: "topP", label: "Top P", min: 0.01, max: 1, step: 0.05, initial: 0.8 },
  {
    key: "maxTokens",
    label: "最大输出 Token",
    min: 64,
    max: 8192,
    step: 64,
    initial: 2048,
  },
  {
    key: "presencePenalty",
    label: "Presence penalty",
    min: -2,
    max: 2,
    step: 0.1,
    initial: 0,
  },
  {
    key: "frequencyPenalty",
    label: "Frequency penalty",
    min: -2,
    max: 2,
    step: 0.1,
    initial: 0,
  },
];
export function GenerationFields() {
  const form = Form.useFormInstance();
  const values = Form.useWatch("generation", form) as
    GenerationConfig | undefined;
  return (
    <>
      <p className="muted">
        关闭覆盖时使用模型默认值。各模型支持的范围会在后端接入时按能力校验。
      </p>
      {fields.map((f) => (
        <div className="generation-row" key={f.key}>
          <label htmlFor={`generation-${f.key}`}>{f.label}</label>
          <Switch
            aria-label={`覆盖 ${f.label}`}
            checked={values?.[f.key] != null}
            onChange={(enabled) =>
              form.setFieldValue(
                ["generation", f.key],
                enabled ? f.initial : null,
              )
            }
          />
          <Form.Item name={["generation", f.key]} noStyle>
            <InputNumber
              id={`generation-${f.key}`}
              aria-label={f.label}
              disabled={values?.[f.key] == null}
              min={f.min}
              max={f.max}
              step={f.step}
              placeholder="模型默认"
            />
          </Form.Item>
          <small className="muted">
            {values?.[f.key] != null ? "显式覆盖" : "模型默认"}
          </small>
        </div>
      ))}
    </>
  );
}
