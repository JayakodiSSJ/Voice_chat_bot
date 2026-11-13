// import React, { useState } from "react";
// import Navbar from "./Navbar";
// import Footer from "./Footer";
// import { URL } from "../urls";
// import "./Contact.css";

// /* ---------------- Contact Page ---------------- */

// const Contact = () => {
//     const [formData, setFormData] = useState({
//         name: "",
//         email: "",
//         subject: "",
//         message: "",
//         category: "general",
//     });
//     const [isSubmitting, setIsSubmitting] = useState(false);
//     const [submitStatus, setSubmitStatus] = useState({ type: "", message: "" });

//     const handleSubmit = async (e) => {
//         e.preventDefault();
//         setIsSubmitting(true);
//         setSubmitStatus({ type: "", message: "" });

//         try {
//             const response = await fetch(URL.CONTACT_SUBMIT, {
//                 method: "POST",
//                 headers: {
//                     "Content-Type": "application/json",
//                 },
//                 body: JSON.stringify(formData),
//             });

//             const result = await response.json().catch(() => ({}));

//             if (!response.ok) {
//                 throw new Error(result.error || "Failed to submit your message");
//             }

//             const ackSent = result.acknowledgment_sent !== false;
//             const ackError = result.acknowledgment_error;
//             setSubmitStatus({
//                 type: "success",
//                 message: ackSent
//                     ? "Thank you. Your inquiry was submitted successfully."
//                     : `Thank you. Your inquiry was submitted successfully. We could not confirm acknowledgment email delivery right now, but our team has received your request.${ackError ? ` (${ackError})` : ""}`,
//             });
//             setFormData({
//                 name: "",
//                 email: "",
//                 subject: "",
//                 message: "",
//                 category: "general",
//             });
//         } catch (error) {
//             setSubmitStatus({
//                 type: "error",
//                 message: error.message || "Something went wrong. Please try again.",
//             });
//         } finally {
//             setIsSubmitting(false);
//         }
//     };

//     const handleChange = (e) => {
//         setFormData({
//             ...formData,
//             [e.target.name]: e.target.value,
//         });
//     };

//     return (
//         <div style={{ width: "100vw", minHeight: "100vh" }}>
//             <div
//                 style={{
//                     minHeight: "100vh",
//                     width: "100%",
//                     fontFamily: "Arial, sans-serif",
//                     color: "#fff",
//                 }}
//             >
//                 <Navbar />

//                 <section style={{ textAlign: "center", padding: "4rem 1rem" }}>
//                     <h1
//                         style={{
//                             fontSize: "3rem",
//                             fontWeight: "bold",
//                             marginBottom: "1rem",
//                         }}
//                     >
//                         Get in <span style={{ color: "#8b5cf6" }}>Touch</span>
//                     </h1>
//                     <p
//                         style={{
//                             fontSize: "1.2rem",
//                             maxWidth: "700px",
//                             margin: "0 auto",
//                         }}
//                     >
//                         Have questions about our AI agents or need help getting started? Our
//                         team is here to help you succeed with your automation journey.
//                     </p>
//                 </section>

//                 <section
//                     style={{ padding: "2rem 1rem", maxWidth: "800px", margin: "0 auto" }}
//                 >
//                     <h2
//                         style={{
//                             fontSize: "2rem",
//                             marginBottom: "1.5rem",
//                             textAlign: "center",
//                         }}
//                     >
//                         Send Us a Message
//                     </h2>
//                     <form onSubmit={handleSubmit}>
//                         {submitStatus.message && (
//                             <div
//                                 style={{
//                                     marginBottom: "1rem",
//                                     padding: "0.75rem 1rem",
//                                     borderRadius: "8px",
//                                     background:
//                                         submitStatus.type === "success"
//                                             ? "rgba(16, 185, 129, 0.2)"
//                                             : "rgba(239, 68, 68, 0.2)",
//                                     border:
//                                         submitStatus.type === "success"
//                                             ? "1px solid rgba(16, 185, 129, 0.5)"
//                                             : "1px solid rgba(239, 68, 68, 0.5)",
//                                     color: "#fff",
//                                 }}
//                             >
//                                 {submitStatus.message}
//                             </div>
//                         )}
//                         <div style={{ marginBottom: "1.2rem" }}>
//                             <label>Full Name *</label>
//                             <input
//                                 type="text"
//                                 name="name"
//                                 required
//                                 value={formData.name}
//                                 onChange={handleChange}
//                                 style={{
//                                     width: "100%",
//                                     padding: "0.8rem",
//                                     borderRadius: "8px",
//                                     border: "1px solid rgba(255,255,255,0.3)",
//                                     background: "rgba(255,255,255,0.1)",
//                                     color: "white",
//                                 }}
//                             />
//                         </div>
//                         <div style={{ marginBottom: "1.2rem" }}>
//                             <label>Email Address *</label>
//                             <input
//                                 type="email"
//                                 name="email"
//                                 required
//                                 value={formData.email}
//                                 onChange={handleChange}
//                                 style={{
//                                     width: "100%",
//                                     padding: "0.8rem",
//                                     borderRadius: "8px",
//                                     border: "1px solid rgba(255,255,255,0.3)",
//                                     background: "rgba(255,255,255,0.1)",
//                                     color: "white",
//                                 }}
//                             />
//                         </div>
//                         <div style={{ marginBottom: "1.2rem" }}>
//                             <label>Category</label>
//                             <select
//                                 name="category"
//                                 value={formData.category}
//                                 onChange={handleChange}
//                                 style={{
//                                     width: "100%",
//                                     padding: "0.8rem",
//                                     borderRadius: "8px",
//                                     border: "1px solid rgba(255,255,255,0.3)",
//                                     background: "rgba(255,255,255,0.1)",
//                                     color: "white",
//                                 }}
//                             >
//                                 <option value="general">General Inquiry</option>
//                                 <option value="support">Technical Support</option>
//                                 <option value="sales">Sales Question</option>
//                                 <option value="partnership">Partnership</option>
//                                 <option value="feedback">Feedback</option>
//                             </select>
//                         </div>
//                         <div style={{ marginBottom: "1.2rem" }}>
//                             <label>Subject *</label>
//                             <input
//                                 type="text"
//                                 name="subject"
//                                 required
//                                 value={formData.subject}
//                                 onChange={handleChange}
//                                 style={{
//                                     width: "100%",
//                                     padding: "0.8rem",
//                                     borderRadius: "8px",
//                                     border: "1px solid rgba(255,255,255,0.3)",
//                                     background: "rgba(255,255,255,0.1)",
//                                     color: "white",
//                                 }}
//                             />
//                         </div>
//                         <div style={{ marginBottom: "1.2rem" }}>
//                             <label>Message *</label>
//                             <textarea
//                                 name="message"
//                                 required
//                                 rows={6}
//                                 value={formData.message}
//                                 onChange={handleChange}
//                                 style={{
//                                     width: "100%",
//                                     padding: "0.8rem",
//                                     borderRadius: "8px",
//                                     border: "1px solid rgba(255,255,255,0.3)",
//                                     background: "rgba(255,255,255,0.1)",
//                                     color: "white",
//                                 }}
//                             />
//                         </div>
//                         <div style={{ textAlign: "center" }}>
//                             <button
//                                 type="submit"
//                                 disabled={isSubmitting}
//                                 style={{
//                                     background: "#9333ea",
//                                     padding: "0.8rem 2rem",
//                                     borderRadius: "8px",
//                                     color: "white",
//                                     fontWeight: "bold",
//                                     border: "none",
//                                     cursor: isSubmitting ? "not-allowed" : "pointer",
//                                     opacity: isSubmitting ? 0.7 : 1,
//                                 }}
//                             >
//                                 {isSubmitting ? "Sending..." : "Send Message"}
//                             </button>
//                         </div>
//                     </form>
//                 </section>

//                 <Footer />
//             </div>
//         </div>
//     );
// };

// export default Contact;
