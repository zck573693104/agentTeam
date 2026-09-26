import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import StatusBadge from "./StatusBadge";

describe("StatusBadge", () => {
  it("renders pending status with mapped text", () => {
    render(<StatusBadge status="pending" />);
    expect(screen.getByText("等待中")).toBeInTheDocument();
  });

  it("renders running status with mapped text", () => {
    render(<StatusBadge status="running" />);
    expect(screen.getByText("运行中")).toBeInTheDocument();
  });

  it("renders completed status with mapped text", () => {
    render(<StatusBadge status="completed" />);
    expect(screen.getByText("已完成")).toBeInTheDocument();
  });

  it("renders failed status with mapped text", () => {
    render(<StatusBadge status="failed" />);
    expect(screen.getByText("失败")).toBeInTheDocument();
  });

  it("renders interrupted status with mapped text", () => {
    render(<StatusBadge status="interrupted" />);
    expect(screen.getByText("待审批")).toBeInTheDocument();
  });

  it("renders cancelling status with mapped text", () => {
    render(<StatusBadge status="cancelling" />);
    expect(screen.getByText("取消中")).toBeInTheDocument();
  });

  it("renders cancelled status with mapped text", () => {
    render(<StatusBadge status="cancelled" />);
    expect(screen.getByText("已取消")).toBeInTheDocument();
  });

  it("falls back to raw status text when unknown", () => {
    render(<StatusBadge status="weird-state" />);
    expect(screen.getByText("weird-state")).toBeInTheDocument();
  });
});
