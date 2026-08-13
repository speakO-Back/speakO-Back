package com.example.speako.domain.slide.repository;

import com.example.speako.domain.presentation.entity.Presentation;
import com.example.speako.domain.presentation.entity.Slide;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface SlideRepository extends JpaRepository<Slide, Long> {

    List<Slide> findByPresentation(Presentation presentation);

}