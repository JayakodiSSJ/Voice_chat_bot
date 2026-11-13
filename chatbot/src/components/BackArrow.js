import React from "react";
import { FaArrowLeft } from "react-icons/fa";
import "./BackArrow.css";

const BackArrow = ({ onClick, className = "", title = "Go back", style = {} }) => {
  return (
    <button
      type="button"
      className={`back-arrow ${className}`.trim()}
      onClick={onClick}
      aria-label={title}
      title={title}
      style={style}
    >
      <FaArrowLeft />
    </button>
  );
};

export default BackArrow;
