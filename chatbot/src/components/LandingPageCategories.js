import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import Navbar from "./Navbar";
import Footer from "./Footer";

/* ---------------- Header Component ---------------- */
// const Header = ({ user, onSignOut, currentPage = "categories" }) => {
//   const navigate = useNavigate();
//   const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);

//   const getNavLinkStyle = (page) => ({
//     fontSize: "16px",
//     margin: "0 12px",
//     color: "#9333ea",
//     textTransform: "capitalize",
//     background: "none",
//     border: "none",
//     cursor: "pointer",
//     borderBottom: currentPage === page ? "2px solid #a855f7" : "none",
//   });

//   const getMobileNavLinkStyle = (page) => ({
//     color: currentPage === page ? "#fff" : "#cfcfcf",
//     fontSize: "18px",
//     background: "none",
//     border: "none",
//     cursor: "pointer",
//     textAlign: "left",
//     fontWeight: currentPage === page ? "bold" : "normal",
//   });

//   return (
//     <header
//       style={{
//         position: "sticky",
//         top: 0,
//         zIndex: 1000,
//         backgroundColor: "white",
//         padding: "15px 20px",
//       }}
//     >
//       <div
//         style={{
//           width: "100%",
//           display: "flex",
//           justifyContent: "space-between",
//           alignItems: "center",
//         }}
//       >
//         {/* Left: Logo + Desktop Nav */}
//         <div style={{ display: "flex", alignItems: "center" }}>
//           <button
//             onClick={() => navigate("/")}
//             style={{
//               fontSize: "22px",
//               fontWeight: "bold",
//               color: "#9333ea",
//               background: "none",
//               border: "none",
//               cursor: "pointer",
//             }}
//           >
//             Aisle Agent
//           </button>
//           <nav
//             style={{
//               display: window.innerWidth > 768 ? "flex" : "none",
//               marginLeft: "20px",
//             }}
//           >
//             {["home", "categories", "pricing", "about", "contact"].map(
//               (page) => (
//                 <button
//                   key={page}
//                   onClick={() => navigate(`/${page === "home" ? "" : page}`)}
//                   style={getNavLinkStyle(page)}
//                 >
//                   {page.charAt(0).toUpperCase() + page.slice(1)}
//                 </button>
//               )
//             )}
//           </nav>
//         </div>

//         {/* Right: Auth + Mobile Menu Button */}
//         <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
//           {user ? (
//             <button
//               onClick={onSignOut}
//               style={{
//                 background: "linear-gradient(135deg, #9333ea, #a855f7)",
//                 padding: "10px 20px",
//                 borderRadius: "8px",
//                 fontWeight: "600",
//                 color: "#fff",
//                 border: "none",
//                 cursor: "pointer",
//               }}
//             >
//               Sign Out
//             </button>
//           ) : (
//             <button
//               onClick={() => navigate("/prelogin")}
//               style={{
//                 background: "linear-gradient(135deg, #9333ea, #a855f7)",
//                 padding: "10px 20px",
//                 borderRadius: "8px",
//                 fontWeight: "600",
//                 color: "#fff",
//                 border: "none",
//                 cursor: "pointer",
//               }}
//             >
//               Browse Agents
//             </button>
//           )}
//           <button
//             onClick={() => setIsMobileMenuOpen(!isMobileMenuOpen)}
//             style={{
//               display: window.innerWidth <= 768 ? "inline-block" : "none",
//               fontSize: "24px",
//               background: "none",
//               border: "none",
//               cursor: "pointer",
//               color: "#9333ea",
//             }}
//           >
//             {isMobileMenuOpen ? "✖" : "☰"}
//           </button>
//         </div>
//       </div>

//       {/* Mobile Menu */}
//       {isMobileMenuOpen && (
//         <div
//           style={{
//             marginTop: "10px",
//             background: "rgba(20,20,40,0.95)",
//             borderRadius: "12px",
//             padding: "20px",
//           }}
//         >
//           <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
//             <input
//               type="text"
//               placeholder="Search agents..."
//               style={{
//                 width: "80%",
//                 padding: "5px",
//                 borderRadius: "6px",
//                 border: "none",
//               }}
//             />
//             <span>🔍</span>
//           </div>
//           <nav
//             style={{
//               display: "flex",
//               flexDirection: "column",
//               gap: "10px",
//               marginTop: "15px",
//             }}
//           >
//             {["home", "categories", "pricing", "about", "contact"].map(
//               (page) => (
//                 <button
//                   key={page}
//                   onClick={() => {
//                     navigate(`/${page === "home" ? "" : page}`);
//                     setIsMobileMenuOpen(false);
//                   }}
//                   style={getMobileNavLinkStyle(page)}
//                 >
//                   {page.charAt(0).toUpperCase() + page.slice(1)}
//                 </button>
//               )
//             )}
//           </nav>
//         </div>
//       )}
//     </header>
//   );
// };

/* ---------------- Footer Component ---------------- */
// const Footer = () => (
//   <footer
//     style={{
//       width: "100%",
//       backgroundColor: "#1a1a2e",
//       color: "#fff",
//       textAlign: "center",
//       padding: "20px 0",
//       marginTop: "auto",
//     }}
//   >
//     <div
//       style={{
//         maxWidth: "1200px",
//         margin: "0 auto",
//         padding: "0 20px",
//       }}
//     >
//       <p style={{ margin: 0, fontSize: "14px" }}>
//         Powered By{" "}
//         <span style={{ fontWeight: "bold", color: "#8b5cf6" }}>
//           SLT Digital Lab
//         </span>{" "}
//         | @2025
//       </p>
//     </div>
//   </footer>
// );

/* ---------------- Categories Page ---------------- */
const Categories = () => {
  const [searchQuery, setSearchQuery] = useState("");

  const categories = [
    {
      id: "productivity",
      name: "Productivity",
      description:
        "Automate tasks, manage schedules, and boost efficiency with AI-powered productivity tools.",
      icon: "💼",
      count: 1203,
      featured: true,
    },
    {
      id: "marketing",
      name: "Marketing",
      description:
        "Generate campaigns, analyze metrics, and optimize your marketing strategy with AI assistance.",
      icon: "📈",
      count: 892,
      featured: true,
    },
    {
      id: "development",
      name: "Development",
      description:
        "Code review, debugging, testing, and development workflow automation tools.",
      icon: "💻",
      count: 756,
      featured: true,
    },
    {
      id: "design",
      name: "Design",
      description:
        "Create stunning visuals, logos, and brand materials with AI-powered design tools.",
      icon: "🎨",
      count: 645,
      featured: true,
    },
    {
      id: "analytics",
      name: "Analytics",
      description:
        "Transform data into insights with advanced visualization and analysis tools.",
      icon: "📊",
      count: 534,
      featured: false,
    },
    {
      id: "customer-service",
      name: "Customer Service",
      description:
        "Build chatbots, automate support, and enhance customer experience.",
      icon: "🎧",
      count: 421,
      featured: false,
    },
    {
      id: "content",
      name: "Content Creation",
      description:
        "Generate articles, social media posts, and marketing content with AI writers.",
      icon: "✍",
      count: 387,
      featured: false,
    },
    {
      id: "finance",
      name: "Finance",
      description:
        "Manage budgets, track expenses, and get investment insights with financial AI.",
      icon: "💰",
      count: 298,
      featured: false,
    },
  ];

  const filteredCategories = categories.filter(
    (category) =>
      category.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      category.description.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const featuredCategories = categories.filter((cat) => cat.featured);

  const glassStyle = {
    background: "rgba(255, 255, 255, 0.1)",
    borderRadius: "1rem",
    border: "1px solid rgba(255, 255, 255, 0.2)",
    backdropFilter: "blur(10px)",
    boxShadow: "0 4px 20px rgba(0, 0, 0, 0.3)",
    padding: "2rem",
  };

  return (
    <div style={{ width: "100vw", minHeight: "100vh", display: "flex", flexDirection: "column" }}>
      <div
        style={{
          // background: "linear-gradient(135deg, #1e003d, #34006f)",
          minHeight: "100vh",
          flexGrow: 1,
          width: "100%",
          fontFamily: "Arial, sans-serif",
          color: "#fff",
          /*padding: "10px",*/
        }}
      >
        {/* Header */}
        {/* <Header currentPage="categories" /> */}
        <Navbar/>

        {/* Hero Section */}
        <section style={{ textAlign: "center", margin: "40px 0" }}>
          <h1 style={{ fontSize: "3rem", marginBottom: "20px" }}>
            Explore <span style={{ color: "#8b5cf6" }}>Categories</span>
          </h1>
          <p
            style={{ fontSize: "1.2rem", maxWidth: "800px", margin: "0 auto" }}
          >
            Discover AI agents organized by industry and use case. Find the
            perfect solution for your specific needs across our comprehensive
            category collection.
          </p>

          {/* Search */}
          <div
            style={{
              display: "flex",
              justifyContent: "center",
              marginTop: "2rem",
              position: "relative",
            }}
          >
            <input
              type="text"
              placeholder="Search categories..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              style={{
                width: "60%",
                maxWidth: "600px",
                padding: "1rem 3rem 1rem 1.2rem",
                fontSize: "1rem",
                borderRadius: "2rem",
                border: "1px solid rgba(255, 255, 255, 0.2)",
                background: "rgba(255, 255, 255, 0.1)",
                backdropFilter: "blur(10px)",
                color: "#fff",
                outline: "none",
              }}
            />
            <span
              style={{
                position: "absolute",
                right: "22%",
                top: "50%",
                transform: "translateY(-50%)",
                fontSize: "1.2rem",
                color: "rgba(255, 255, 255, 0.8)",
              }}
            >
              🔍
            </span>
          </div>
        </section>

        {/* Featured Categories */}
        <section>
          <div style={{ textAlign: "center", marginBottom: "40px" }}>
            <h2 style={{ fontSize: "2rem", fontWeight: "bold" }}>
              ⭐ Featured Categories
            </h2>
            <p style={{ maxWidth: "600px", margin: "0 auto", lineHeight: 1.6 }}>
              Our most popular categories with the highest-rated agents and
              active communities.
            </p>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
              gap: "15px",
              padding: "0 25px",
            }}
          >
            {featuredCategories.map((category) => (
              <div key={category.id} style={glassStyle}>
                <h3 style={{ fontSize: "1.5rem" }}>
                  {category.icon} {category.name}
                </h3>
                <p>{category.description}</p>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    marginTop: "10px",
                  }}
                >
                  <span>{category.count} agents</span>
                  <span>➡</span>
                </div>
              </div>
            ))}
          </div>
        </section>

        {/* All Categories */}
        <section style={{ marginTop: "60px" }}>
          <div style={{ textAlign: "center", marginBottom: "40px" }}>
            <h2 style={{ fontSize: "2rem", fontWeight: "bold" }}>
              All Categories
            </h2>
            <p style={{ maxWidth: "600px", margin: "0 auto", lineHeight: 1.6 }}>
              Browse our complete collection of AI agent categories.
            </p>
          </div>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit,minmax(250px,1fr))",
              gap: "20px",
              padding: "0 25px",
            }}
          >
            {filteredCategories.map((category) => (
              <div key={category.id} style={glassStyle}>
                <h3 style={{ fontSize: "1.5rem" }}>
                  {category.icon} {category.name}
                </h3>
                <p>{category.description}</p>
                <div
                  style={{
                    display: "flex",
                    justifyContent: "space-between",
                    marginTop: "10px",
                  }}
                >
                  <span>{category.count} agents</span>
                  <span>➡</span>
                </div>
              </div>
            ))}
          </div>

          {filteredCategories.length === 0 && (
            <div
              style={{ ...glassStyle, textAlign: "center", marginTop: "20px" }}
            >
              <p>No categories found matching your search.</p>
              <button
                onClick={() => setSearchQuery("")}
                style={{
                  marginTop: "10px",
                  padding: "10px 20px",
                  borderRadius: "8px",
                  border: "none",
                  background: "linear-gradient(135deg, #9333ea, #a855f7)",
                  color: "#fff",
                  cursor: "pointer",
                }}
              >
                Clear search
              </button>
            </div>
          )}
        </section>

        {/* Stats Section */}
        <section style={{ marginTop: "60px", textAlign: "center" }}>
          <h2 style={{ fontSize: "2rem", fontWeight: "bold" }}>
            📊 Platform Statistics
          </h2>
          <p style={{ maxWidth: "600px", margin: "0 auto", lineHeight: 1.6 }}>
            See how our community is growing across all categories.
          </p>

          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))",
              gap: "20px",
              marginTop: "20px",
              padding: "0 25px",
            }}
          >
            <div style={glassStyle}>
              <div
                style={{
                  fontSize: "1.5rem",
                  fontWeight: "bold",
                  color: "#60a5fa",
                }}
              >
                8
              </div>
              <div>Categories</div>
            </div>
            <div style={glassStyle}>
              <div
                style={{
                  fontSize: "1.5rem",
                  fontWeight: "bold",
                  color: "#a78bfa",
                }}
              >
                5,234
              </div>
              <div>Total Agents</div>
            </div>
            <div style={glassStyle}>
              <div
                style={{
                  fontSize: "1.5rem",
                  fontWeight: "bold",
                  color: "#34d399",
                }}
              >
                50K+
              </div>
              <div>Active Users</div>
            </div>
            <div style={glassStyle}>
              <div
                style={{
                  fontSize: "1.5rem",
                  fontWeight: "bold",
                  color: "#fbbf24",
                }}
              >
                10M+
              </div>
              <div>Tasks Completed</div>
            </div>
          </div>
        </section>

        
      
    
      </div>
      {/* Footer */}
      <Footer />
    </div>
  );
};

export default Categories;