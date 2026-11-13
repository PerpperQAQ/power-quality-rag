import { Component } from "react";
import type { PropsWithChildren } from "react";
import { Button, Result } from "antd";

/** A failed route import must leave a useful recovery screen and navigation. */
export class PageLoadBoundary extends Component<PropsWithChildren, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  render() {
    if (this.state.failed) return <Result status="warning" title="页面暂时无法加载"
      subTitle="页面资源可能已更新，请重新加载后继续。已保存的内容不受影响。"
      extra={<Button type="primary" onClick={() => window.location.reload()}>重新加载页面</Button>} />;
    return this.props.children;
  }
}
