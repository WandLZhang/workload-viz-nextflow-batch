/**
 * @file CostInfoTooltip.tsx
 * @brief Cost breakdown tooltip showing GCP SKUs and estimated costs for the Nextflow pipeline.
 *
 * @details Displays an "i" info icon in the top-right corner of the flowchart viewport.
 * On hover, it expands to show a detailed cost breakdown with actual GCP SKU IDs
 * and pricing for the rnaseq-nf pipeline running on Google Cloud Batch with Spot VMs.
 *
 * @author Willis Zhang
 * @date 2026-02-02
 */

import React, { useState } from 'react';
import './CostInfoTooltip.css';

interface SkuInfo {
  skuId: string;
  description: string;
  price: string;
  unit: string;
}

interface CostLineItem {
  component: string;
  resource: string;
  time: string;
  cost: string;
}

const COMPUTE_SKUS: SkuInfo[] = [
  {
    skuId: 'F179-E1EA-D97A',
    description: 'Spot Preemptible E2 Instance Core (Americas)',
    price: '$0.01007',
    unit: 'vCPU-hour',
  },
  {
    skuId: '9B1F-1E62-4061',
    description: 'Spot Preemptible E2 Instance Ram (Americas)',
    price: '$0.00135',
    unit: 'GiB-hour',
  },
];

const STORAGE_SKUS: SkuInfo[] = [
  {
    skuId: 'E5F0-6A5D-7BAD',
    description: 'Standard Storage US Regional',
    price: '$0.02',
    unit: 'GiB/month',
  },
];

const COST_BREAKDOWN: CostLineItem[] = [
  {
    component: 'FASTQC',
    resource: '2 vCPU, 4 GiB',
    time: '~3 min',
    cost: '$0.0013',
  },
  {
    component: 'QUANT',
    resource: '2 vCPU, 4 GiB',
    time: '~3 min',
    cost: '$0.0013',
  },
  {
    component: 'MULTIQC',
    resource: '1 vCPU, 2 GiB',
    time: '~2 min',
    cost: '$0.0004',
  },
  {
    component: 'Storage',
    resource: '~100 MB scratch',
    time: '1 month',
    cost: '$0.002',
  },
];

export const CostInfoTooltip: React.FC = () => {
  const [isExpanded, setIsExpanded] = useState(false);

  const handleMouseEnter = () => setIsExpanded(true);
  const handleMouseLeave = () => setIsExpanded(false);

  const totalCost = COST_BREAKDOWN.reduce((sum, item) => {
    return sum + parseFloat(item.cost.replace('$', ''));
  }, 0);

  return (
    <div
      className="cost-info-tooltip-wrapper"
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
    >
      <button className="cost-info-button" aria-label="View cost breakdown">
        <span className="material-symbols-outlined">info</span>
      </button>

      {isExpanded && (
        <div className="cost-info-panel">
          <div className="cost-info-header">
            <span className="material-symbols-outlined cost-icon">payments</span>
            <h3>Estimated Cost Breakdown</h3>
          </div>

          <div className="cost-info-section">
            <h4>
              <span className="material-symbols-outlined section-icon">memory</span>
              Compute (Spot Preemptible E2)
            </h4>
            <table className="sku-table">
              <thead>
                <tr>
                  <th>SKU ID</th>
                  <th>Description</th>
                  <th>Price</th>
                </tr>
              </thead>
              <tbody>
                {COMPUTE_SKUS.map((sku) => (
                  <tr key={sku.skuId}>
                    <td className="sku-id">{sku.skuId}</td>
                    <td>{sku.description}</td>
                    <td className="price">{sku.price}/{sku.unit}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="cost-info-section">
            <h4>
              <span className="material-symbols-outlined section-icon">cloud_upload</span>
              Cloud Storage
            </h4>
            <table className="sku-table">
              <thead>
                <tr>
                  <th>SKU ID</th>
                  <th>Description</th>
                  <th>Price</th>
                </tr>
              </thead>
              <tbody>
                {STORAGE_SKUS.map((sku) => (
                  <tr key={sku.skuId}>
                    <td className="sku-id">{sku.skuId}</td>
                    <td>{sku.description}</td>
                    <td className="price">{sku.price}/{sku.unit}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="cost-info-section">
            <h4>
              <span className="material-symbols-outlined section-icon">calculate</span>
              Pipeline Cost Estimate (rnaseq-nf)
            </h4>
            <table className="cost-table">
              <thead>
                <tr>
                  <th>Component</th>
                  <th>Resource</th>
                  <th>Time</th>
                  <th>Cost</th>
                </tr>
              </thead>
              <tbody>
                {COST_BREAKDOWN.map((item) => (
                  <tr key={item.component}>
                    <td className="component">{item.component}</td>
                    <td>{item.resource}</td>
                    <td>{item.time}</td>
                    <td className="cost">{item.cost}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="cost-total">
            <div className="total-row">
              <span className="total-label">
                <span className="material-symbols-outlined">target</span>
                Total Estimated Cost:
              </span>
              <span className="total-value">~${totalCost.toFixed(4)}</span>
            </div>
            <div className="total-note">Less than 1 cent per run!</div>
          </div>

          <div className="cost-info-footer">
            <div className="batch-note">
              <span className="material-symbols-outlined">check_circle</span>
              Google Cloud Batch: <strong>FREE</strong> (no orchestration charge)
            </div>
            <a
              href="https://cloud.google.com/skus?hl=en&filter=preemptible%20e2"
              target="_blank"
              rel="noopener noreferrer"
              className="sku-link"
            >
              <span className="material-symbols-outlined">open_in_new</span>
              View GCP SKU Catalog
            </a>
          </div>
        </div>
      )}
    </div>
  );
};
