package com.example.speako.domain.presentation.repository;

import com.example.speako.domain.presentation.entity.Presentation;
import org.springframework.data.jpa.repository.JpaRepository;

public interface PresentationRepository extends JpaRepository<Presentation, Long> {

}