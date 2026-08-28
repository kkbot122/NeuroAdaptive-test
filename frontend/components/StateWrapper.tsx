import { Loader2, AlertCircle, Lock, LayoutTemplate } from "lucide-react";

interface StateWrapperProps {
  isLoading: boolean;
  isError: boolean;
  isEmpty: boolean;
  isUnauthorized?: boolean;
  errorMessage?: string;
  emptyMessage?: string;
  onRetry?: () => void;
  children: React.ReactNode;
}

export function StateWrapper({
  isLoading,
  isError,
  isEmpty,
  isUnauthorized,
  errorMessage = "Something went wrong. Please try again.",
  emptyMessage = "No data available.",
  onRetry,
  children,
}: StateWrapperProps) {
  if (isLoading) {
    return (
      <div className="flex flex-col items-center justify-center p-12 text-gray-500">
        <Loader2 className="w-10 h-10 animate-spin mb-4 text-purple-600" />
        <p className="font-bold tracking-widest uppercase">Loading...</p>
      </div>
    );
  }

  if (isUnauthorized) {
    return (
      <div className="flex flex-col items-center justify-center p-12 text-center bg-red-50 rounded-xl border-2 border-red-200">
        <Lock className="w-12 h-12 text-red-500 mb-4" />
        <h3 className="text-xl font-bold text-red-700 mb-2">Access Denied</h3>
        <p className="text-red-600">You don&apos;t have permission to view this content.</p>
      </div>
    );
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center p-12 text-center bg-red-50 rounded-xl border-2 border-red-200">
        <AlertCircle className="w-12 h-12 text-red-500 mb-4" />
        <h3 className="text-xl font-bold text-red-700 mb-2">Error</h3>
        <p className="text-red-600 mb-6">{errorMessage}</p>
        {onRetry && (
          <button
            onClick={onRetry}
            className="bg-white border-2 border-red-500 text-red-600 px-6 py-2 rounded-lg font-bold shadow-[4px_4px_0px_0px_rgba(239,68,68,0.3)] transition-all active:translate-x-1 active:translate-y-1 active:shadow-none hover:bg-red-50"
          >
            Try Again
          </button>
        )}
      </div>
    );
  }

  if (isEmpty) {
    return (
      <div className="flex flex-col items-center justify-center p-16 text-center bg-white rounded-xl border-2 border-black border-dashed opacity-70">
        <LayoutTemplate className="w-12 h-12 text-gray-400 mb-4" />
        <h3 className="text-xl font-bold text-gray-600 mb-2">Nothing Here Yet</h3>
        <p className="text-gray-500 max-w-md">{emptyMessage}</p>
      </div>
    );
  }

  return <>{children}</>;
}
