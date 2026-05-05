import React from 'react';
import { Wrench } from 'lucide-react';

interface RsaSettingsPanelProps {
  useRsa: boolean;
  onUseRsaChange: (value: boolean) => void;
  rsaMode: 'standalone' | 'rag';
  onRsaModeChange: (mode: 'standalone' | 'rag') => void;
  rsaN: number;
  onRsaNChange: (value: number) => void;
  rsaK: number;
  onRsaKChange: (value: number) => void;
  rsaT: number;
  onRsaTChange: (value: number) => void;
}

// Custom hook to manage number input with validation
const useNumberInput = (value: number, onChange: (val: number) => void, defaultValue: number) => {
  const [input, setInput] = React.useState(value.toString());

  React.useEffect(() => {
    setInput(value.toString());
  }, [value]);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setInput(e.target.value);
    const num = parseInt(e.target.value);
    if (!isNaN(num) && num >= 1) {
      onChange(num);
    }
  };

  const handleBlur = () => {
    const num = parseInt(input);
    if (isNaN(num) || num < 1) {
      onChange(defaultValue);
      setInput(defaultValue.toString());
    }
  };

  return { input, handleChange, handleBlur };
};

export const RsaSettingsPanel: React.FC<RsaSettingsPanelProps> = ({
  useRsa,
  onUseRsaChange,
  rsaMode,
  onRsaModeChange,
  rsaN,
  onRsaNChange,
  rsaK,
  onRsaKChange,
  rsaT,
  onRsaTChange,
}) => {
  const nState = useNumberInput(rsaN, onRsaNChange, 8);
  const kState = useNumberInput(rsaK, onRsaKChange, 4);
  const tState = useNumberInput(rsaT, onRsaTChange, 3);

  return (
    <div className="glass-panel">
      <div className="flex items-start gap-4">
        <input
          type="checkbox"
          checked={useRsa}
          onChange={(e) => onUseRsaChange(e.target.checked)}
          className="form-checkbox mt-1"
          id="use-rsa"
        />
        <div className="flex-1">
          <label
            htmlFor="use-rsa"
            className="form-label-block cursor-pointer flex items-center gap-2"
          >
            <Wrench className="w-4 h-4" />
            Enable RSA Mode
          </label>
          <p className="text-sm text-tertiary mt-1">
            Enable Recursive Self-Aggregation for enhanced retrosynthesis planning. RSA generates
            multiple proposals and iteratively refines them for better results.
          </p>
        </div>
      </div>

      {/* RSA Configuration */}
      {useRsa && (
        <div className="mt-6 pl-8 space-y-4">
          {/* RSA Mode */}
          <div>
            <label className="form-label">Mode</label>
            <select
              value={rsaMode}
              onChange={(e) => onRsaModeChange(e.target.value as 'standalone' | 'rag')}
              className="form-select w-full"
            >
              <option value="standalone">Standalone (AI-first)</option>
              <option value="rag">RAG (Database-informed)</option>
            </select>
            <p className="text-xs text-tertiary mt-1">
              {rsaMode === 'standalone'
                ? 'Uses pure chemistry reasoning without database lookups'
                : 'Incorporates known reactions from databases to guide synthesis'}
            </p>
          </div>

          {/* RSA Parameters Grid */}
          <div className="grid grid-cols-3 gap-4">
            <div>
              <label className="form-label">N (Proposals)</label>
              <input
                type="number"
                value={nState.input}
                onChange={nState.handleChange}
                onBlur={nState.handleBlur}
                min="1"
                max="20"
                step="1"
                className="form-input w-full"
                style={
                  {
                    appearance: 'auto',
                  } as React.CSSProperties
                }
              />
              <p className="text-xs text-tertiary mt-1">Number of initial proposals to generate</p>
            </div>

            <div>
              <label className="form-label">K (Subset)</label>
              <input
                type="number"
                value={kState.input}
                onChange={kState.handleChange}
                onBlur={kState.handleBlur}
                min="1"
                max={rsaN}
                step="1"
                className="form-input w-full"
                style={
                  {
                    appearance: 'auto',
                  } as React.CSSProperties
                }
              />
              <p className="text-xs text-tertiary mt-1">Subset size for aggregation (K ≤ N)</p>
            </div>

            <div>
              <label className="form-label">T (Steps)</label>
              <input
                type="number"
                value={tState.input}
                onChange={tState.handleChange}
                onBlur={tState.handleBlur}
                min="1"
                max="10"
                step="1"
                className="form-input w-full"
                style={
                  {
                    appearance: 'auto',
                  } as React.CSSProperties
                }
              />
              <p className="text-xs text-tertiary mt-1">Total aggregation steps</p>
            </div>
          </div>

          {/* Runtime Estimate */}
          <div className="glass-panel bg-blue-500/10 border border-blue-500/20">
            <div className="text-sm text-secondary">
              <span className="font-semibold">Estimated Runtime:</span> ~{rsaN * rsaT} AI inferences
            </div>
            <p className="text-xs text-tertiary mt-1">
              Higher values will produce more comprehensive results but take longer to compute.
            </p>
          </div>

          {/* RSA Information */}
          <div className="glass-panel bg-primary/5 border border-primary/20">
            <div className="text-sm text-secondary">
              <p className="font-semibold mb-2">About RSA Mode:</p>
              <ul className="list-disc list-inside space-y-1 text-xs text-tertiary">
                <li>RSA enhances AI predictions through iterative refinement</li>
                <li>Standalone mode relies on pure chemistry knowledge</li>
                <li>RAG mode combines AI with database lookups for better accuracy</li>
                <li>N controls the breadth of exploration (more proposals = wider search)</li>
                <li>K controls aggregation subset size (larger = more consensus)</li>
                <li>T controls iteration depth (more steps = more refinement)</li>
              </ul>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
