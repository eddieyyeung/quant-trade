import { Button, Result } from 'antd';
import { Link, useLocation } from 'react-router-dom';

/** Shown for a URL that matches no section. */
export default function NotFound() {
  const location = useLocation();
  return (
    <Result
      status="404"
      title="页面不存在"
      subTitle={location.pathname}
      extra={
        <Link to="/data">
          <Button type="primary">回到数据总览</Button>
        </Link>
      }
    />
  );
}
