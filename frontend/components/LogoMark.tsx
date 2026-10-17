import Image from "next/image";

export default function LogoMark({ className = "" }: { className?: string }) {
  return (
    <span className={`nl-logo-mark ${className}`} aria-hidden="true">
      <Image
        src="/neurolearn-logo.png"
        alt=""
        width={2000}
        height={2000}
        sizes="90px"
        className="nl-logo-mark-image"
      />
    </span>
  );
}
