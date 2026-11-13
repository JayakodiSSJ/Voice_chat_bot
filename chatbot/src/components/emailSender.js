import React from "react";

function EmailSender() {

    const sendEmail = async () => {

        try {

            const response = await fetch("api/send-email", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json"
                },
                body: JSON.stringify({
                    to: "[to_email]", 
                    subject: "Login Request Approved",
                    body: "Admin has approved your login request. You can now login to the platform."
                })
            });

            const data = await response.json();

            console.log(data);

        } catch (error) {
            console.error(error);
        }

    };



}

export default EmailSender;
