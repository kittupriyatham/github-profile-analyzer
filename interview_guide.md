# QIntern'26 Project 25 - Interview Guide

This guide is designed to help you conduct a structured, 45-minute technical and behavioral interview for the **Quantum Machine Learning** interns. Since they all scored very highly (95+), the goal here isn't to test basic syntax, but to test their **intuition, problem-solving skills, and deep understanding** of hybrid architectures.

---

## 1. Introduction & Background (5-10 mins)
*Break the ice and understand their motivation.*

*   **Motivation:** "What drew you specifically to Quantum Machine Learning and Project 25?"
*   **Experience:** "Tell me about a quantum computing project you've worked on recently. What was the hardest bug you had to fix?"
*   **Ecosystem:** "You used Qiskit, PennyLane, and Cirq in the assessment. In your own words, what is the core difference between PennyLane and the others?" 
    *   *(Ideal answer: PennyLane is built around differentiable programming and QNodes, making it native for hybrid ML pipelines with PyTorch/TensorFlow, whereas Qiskit/Cirq are general-purpose quantum SDKs).*

## 2. Deep Dive: The Assessment (15 mins)
*Have their `assessment.ipynb` open. Ask them to explain the code they wrote to ensure they didn't just copy/paste from ChatGPT.*

*   **Challenge 4 (PQC):** "In the 4th challenge, you built a Parameterized Quantum Circuit with an RX layer and a CNOT layer. If we wanted to increase the 'expressibility' of this circuit, what gates would you add?"
    *   *(Ideal answer: Add RY or RZ gates to cover more of the Bloch sphere, or increase the number of layers/depth).*
*   **Barren Plateaus (MCQ 6):** "You correctly identified that Barren Plateaus cause gradients to vanish in deep circuits. If you encounter a Barren Plateau while training a VQC, what are 2 practical ways you could try to fix it?"
    *   *(Ideal answer: Use local cost functions instead of global ones, use layer-wise training, or use a hardware-efficient ansatz with less depth).*
*   **Amplitude Amplification:** "In Grover's algorithm, what does the Diffusion Operator physically do to the amplitudes? Try to explain it to me visually."

## 3. Advanced QML Concepts (10-15 mins)
*Test their intuition on how classical ML and Quantum ML interact.*

*   **Data Encoding:** "Before we can pass an image or a dataset into a quantum circuit, we have to encode it. What is the difference between *Angle Embedding* and *Amplitude Embedding*? Which one requires more qubits?"
    *   *(Ideal answer: Angle embedding maps data to rotation angles (needs $N$ qubits for $N$ features). Amplitude embedding maps data into the amplitudes of a superposition state (needs $\log_2(N)$ qubits), saving qubits but requiring deeper circuits to prepare).*
*   **Gradients:** "Classical neural networks use backpropagation to get gradients. Since we can't backpropagate through a physical quantum chip, how does PennyLane calculate gradients for quantum gates?"
    *   *(Ideal answer: The Parameter-Shift Rule. We shift the gate parameter by $+\pi/2$ and $-\pi/2$, run the circuit twice, and subtract the results to get the exact analytical gradient).*
*   **NISQ Era:** "Our current quantum computers are incredibly noisy. How does hardware noise affect the loss landscape of a Variational Quantum Algorithm?"

## 4. Problem Solving / Whiteboarding (5 mins)
*Throw a hypothetical curveball.*

*   **Scenario:** "Imagine we are building a hybrid neural network for a bank to detect fraud. We have a massive dataset of 1 million rows and 50 features. How would you design the architecture? Where does the quantum part go, and where does the classical part go?"
    *   *(Ideal answer: We can't put 1 million rows into a QPU directly. We should use a classical neural network (like a dense layer) to compress the 50 features down to 4 or 8 features (PCA or Autoencoder), pass those 4 features into a small 4-qubit VQC, and then use classical layers for the final output).*

## 5. Reverse Interview (5 mins)
*   "Do you have any questions for me about the project, the team, or the QIntern program?"
